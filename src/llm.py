"""
One model call, four providers.

    export LLM_PROVIDER=gemini
    export GEMINI_API_KEY=...

Every provider here does the same job: take a prompt, return text. Keeping that
behind one function means the extractor and the drafter never learn which vendor is
answering, so switching costs an environment variable rather than a rewrite.

  anthropic  ANTHROPIC_API_KEY. Paid, prepaid credits.
  gemini     GEMINI_API_KEY from aistudio.google.com. Free tier, no card.
  groq       GROQ_API_KEY from console.groq.com. Free tier, no card, very fast.
  cerebras   CEREBRAS_API_KEY from cloud.cerebras.ai. Free tier, no card. Same
             gpt-oss-120b model as groq, but five times the daily quota and five
             times the per-minute token budget, which is what actually governs
             throughput on a long contract. Free-tier context is capped at 8K,
             which a clause never approaches.
  ollama     nothing. Runs a model on your own machine, no key and no network.

A note worth carrying into the README: free tiers are generally funded by your
prompts being used for training. That is fine for public filings, and it is not fine
for someone's real offer letter. If this is ever deployed for other people, put it on
a paid tier or run ollama locally.
"""

import json
import os
import random
import re
import threading
import time
from pathlib import Path


ENV_SOURCE = {}          # variable name -> where its value came from


def _load_env_file():
    """
    Read .env from the project root if present.

    Exporting a key on the command line puts it in shell history and in any
    screenshot of that terminal. A file that .gitignore already covers is both
    safer and less to remember.

    An earlier version used setdefault here, which meant a stale export from an
    old shell silently shadowed a perfectly good .env. The file was being edited
    and never read. Now the shell still wins, because that is what everyone
    expects, but a mismatch is reported loudly instead of hiding.
    """
    a_path = Path(__file__).resolve().parent.parent / ".env"
    if not a_path.exists():
        return

    for line in a_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip("\"'")
        if not v:
            continue

        a_shell = os.environ.get(k)
        if a_shell is None:
            os.environ[k] = v
            ENV_SOURCE[k] = ".env"
        elif a_shell != v:
            ENV_SOURCE[k] = "shell export (differs from .env)"
        else:
            ENV_SOURCE[k] = "shell export (same as .env)"


_load_env_file()


def env_conflicts():
    """Variables where a shell export is overriding a different value in .env."""
    return [k for k, v in ENV_SOURCE.items() if v.startswith("shell export (differs")]

# Google retires model names faster than anyone else and returns a 404 naming the
# replacement, so the fallback list below is walked in order on a model-not-found.
# Override any of it with LLM_MODEL.
DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-6",
    "gemini": "gemini-3.6-flash",
    "groq": "llama-3.3-70b-versatile",
    "cerebras": "gpt-oss-120b",
    "ollama": "llama3.1",
}

GEMINI_FALLBACKS = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-2.5-flash-lite",
                    "gemini-2.5-flash"]

# Free tiers are rate limited per minute, and a corpus scan will blow straight
# through that. Without pacing, the first handful of calls succeed and every one
# after is refused, which reads as "this contract has no risky clauses" rather than
# "we were throttled". Conservative defaults, override with LLM_RPM.
DEFAULT_RPM = {
    "anthropic": 50,
    "gemini": 12,      # free tier is around 15, leave headroom
    "groq": 25,        # free tier is around 30
    "cerebras": 8,     # free tier is tight on requests, generous on tokens
    "ollama": 600,     # local, the only limit is your machine
}

# Requests per minute is the wrong unit on some free tiers. Groq caps tokens per
# minute, and an extraction prompt carries the taxonomy plus the field list plus the
# clause, so it runs to a couple of thousand tokens. Pacing at 25 requests a minute
# therefore blew straight through an 8000 token budget and produced 429s that looked
# like a quiet corpus. Both limits are now tracked and whichever binds first wins.
DEFAULT_TPM = {
    "anthropic": 200000,
    "gemini": 250000,
    "groq": 7000,      # free tier is 8000, and the estimate is now realistic
    "cerebras": 27000,  # free tier is 30000
    "ollama": 10**9,
}

MAX_RETRIES = 4
RETRY_ON = (429, 500, 502, 503, 504)

# urllib defaults to a User-Agent of "Python-urllib/3.x", which Cloudflare in front
# of some APIs rejects outright with error 1010 before the request ever reaches the
# service. That reads as an auth failure and is not one, so every request here sends
# a normal client string.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

_pace_lock = threading.Lock()
_last_call = [0.0]
_token_window = []          # (timestamp, tokens) inside the trailing minute


def rpm():
    a_env = os.environ.get("LLM_RPM")
    if a_env:
        try:
            return max(1, int(a_env))
        except ValueError:
            pass
    return DEFAULT_RPM.get(provider(), 12)


def tpm():
    a_env = os.environ.get("LLM_TPM")
    if a_env:
        try:
            return max(500, int(a_env))
        except ValueError:
            pass
    return DEFAULT_TPM.get(provider(), 6000)


# Reserving the full max_tokens for every reply wastes about a third of a free tier
# budget. max_tokens is a ceiling, not a forecast: an extraction returns a small JSON
# object, well under two hundred tokens. Reserve a realistic figure and let the
# ceiling stay high for the rare long reply.
TYPICAL_REPLY = 300


def estimate_tokens(as_prompt, max_tokens):
    """About four characters a token, plus a realistic reply, not the ceiling."""
    return len(as_prompt) // 4 + min(max_tokens, TYPICAL_REPLY)


def _pace(n_tokens=0):
    """
    Block until both budgets allow the next call.

    Requests per minute is a fixed gap. Tokens per minute is a rolling window: drop
    anything older than sixty seconds, and if this call would push the window over
    the ceiling, wait for the oldest entries to age out.
    """
    with _pace_lock:
        a_wait = (60.0 / rpm()) - (time.monotonic() - _last_call[0])
        if a_wait > 0:
            time.sleep(a_wait)

        if n_tokens:
            a_cap = tpm()
            while True:
                a_now = time.monotonic()
                _token_window[:] = [(t, n) for t, n in _token_window if a_now - t < 60.0]
                a_used = sum(n for _, n in _token_window)
                if a_used + n_tokens <= a_cap or not _token_window:
                    break
                a_sleep = 60.0 - (a_now - _token_window[0][0]) + 0.5
                time.sleep(max(a_sleep, 0.5))
            _token_window.append((time.monotonic(), n_tokens))

        _last_call[0] = time.monotonic()


def _backoff(attempt):
    """Exponential with jitter, so a burst of workers does not retry in lockstep."""
    return min(2 ** attempt, 30) + random.uniform(0, 1.0)


KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "ollama": None,
}


def provider():
    return os.environ.get("LLM_PROVIDER", "anthropic").strip().lower()


def model_name():
    p = provider()
    a_model = os.environ.get("LLM_MODEL")
    if not a_model:
        return DEFAULT_MODELS.get(p, DEFAULT_MODELS["anthropic"])

    # The same open model is listed under different names by different hosts: Groq
    # serves it as openai/gpt-oss-120b, Cerebras as gpt-oss-120b. A pin written for
    # one provider then fails on the other with "does not have that model", which
    # looks like the model was retired rather than renamed. Strip the vendor prefix
    # for hosts that do not use one.
    if p == "cerebras" and "/" in a_model:
        a_model = a_model.split("/", 1)[1]
    elif p == "groq" and a_model == "gpt-oss-120b":
        a_model = "openai/gpt-oss-120b"
    return a_model


def available():
    """Whether the configured provider has what it needs to make a call."""
    p = provider()
    if p not in DEFAULT_MODELS:
        return False, f"unknown LLM_PROVIDER '{p}'. Options: {', '.join(DEFAULT_MODELS)}"

    env = KEY_ENV[p]
    if env and not os.environ.get(env):
        return False, f"{p} selected but {env} is not set"

    if p == "ollama":
        try:
            import urllib.request
            urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
        except Exception:
            return False, "ollama selected but nothing is listening on localhost:11434"

    return True, f"{p} / {model_name()}"


def complete(as_prompt, max_tokens=800):
    """Prompt in, text out. Raises on failure so callers can degrade explicitly."""
    p, a_model = provider(), model_name()

    a_est = estimate_tokens(as_prompt, max_tokens)

    if p == "anthropic":
        from anthropic import Anthropic
        _pace(a_est)
        r = Anthropic().messages.create(
            model=a_model, max_tokens=max_tokens,
            messages=[{"role": "user", "content": as_prompt}])
        return "".join(b.text for b in r.content if b.type == "text").strip()

    if p == "gemini":
        import urllib.error
        import urllib.request

        # Try the configured model, then fall back. A hardcoded name is a time bomb
        # with this provider: the first version of this shipped gemini-2.5-flash and
        # Google had already closed it to new accounts.
        as_try = [a_model] + [m for m in GEMINI_FALLBACKS if m != a_model] \
            if not os.environ.get("LLM_MODEL") else [a_model]
        a_last = None

        # Gemini 3.x spends tokens on internal reasoning before emitting anything,
        # so a small budget can be consumed entirely by thinking and come back with
        # a candidate that has no parts at all. Ask for no thinking, since this is
        # extraction rather than reasoning, and keep the floor generous.
        as_cfg = {"maxOutputTokens": max(max_tokens, 256), "temperature": 0,
                  "thinkingConfig": {"thinkingBudget": 0}}

        for name in as_try:
            as_url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                      f"{name}:generateContent"
                      f"?key={os.environ['GEMINI_API_KEY'].strip()}")

            as_data = None
            for as_conf in (as_cfg, {k: v for k, v in as_cfg.items()
                                     if k != "thinkingConfig"}):
                a_payload = json.dumps({
                    "contents": [{"parts": [{"text": as_prompt}]}],
                    "generationConfig": as_conf,
                }).encode()
                req = urllib.request.Request(
                    as_url, data=a_payload,
                    headers={"Content-Type": "application/json",
                             "User-Agent": UA})
                a_give_up = False
                for attempt in range(MAX_RETRIES):
                    _pace(a_est)
                    try:
                        with urllib.request.urlopen(req, timeout=90) as r:
                            as_data = json.loads(r.read())
                        break
                    except urllib.error.HTTPError as e:
                        a_body = e.read().decode()[:200]
                        if e.code in (401, 403):
                            raise RuntimeError(
                                "gemini rejected the key. Check GEMINI_API_KEY for a "
                                "doubled prefix or stray characters, then confirm it "
                                "at aistudio.google.com") from None
                        if e.code == 400 and as_conf is as_cfg:
                            a_give_up = True
                            break
                        if e.code in RETRY_ON and attempt < MAX_RETRIES - 1:
                            time.sleep(_backoff(attempt))
                            continue
                        a_last = f"gemini {e.code} on {name}: {a_body}"
                        if e.code == 404:
                            as_data = None
                            a_give_up = True
                            break
                        if e.code == 429:
                            raise RuntimeError(
                                f"gemini rate limit hit after {MAX_RETRIES} retries. "
                                f"Currently pacing at {rpm()} calls a minute; lower it "
                                f"with LLM_RPM, or switch provider in .env.") from None
                        raise RuntimeError(a_last) from None
                    except Exception as e:
                        if attempt < MAX_RETRIES - 1:
                            time.sleep(_backoff(attempt))
                            continue
                        raise RuntimeError(f"gemini on {name}: {e}") from None

                if a_give_up:
                    if as_data is None:
                        break
                    continue

            if as_data is None:
                continue                        # retired model, try the next

            a_text = _gemini_text(as_data)
            if a_text is None:
                a_last = f"gemini returned no text on {name}: {_gemini_why(as_data)}"
                continue
            if name != a_model:
                print(f"  (using {name}; {a_model} was unavailable)")
            return a_text

        raise RuntimeError(a_last or "gemini: no usable model found")

    if p == "groq":
        import urllib.error
        import urllib.request
        # gpt-oss and other reasoning models on Groq emit an analysis pass before
        # the answer. Left at the default that pass eats the token budget and the
        # JSON arrives truncated or wrapped in prose. This is extraction, not
        # reasoning, so ask for the least of it.
        as_payload = {
            "model": a_model, "max_tokens": max_tokens, "temperature": 0,
            "messages": [{"role": "user", "content": as_prompt}],
        }
        if "gpt-oss" in a_model or "reason" in a_model:
            as_payload["reasoning_effort"] = "low"
        as_body = json.dumps(as_payload).encode()
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions", data=as_body,
            headers={"Content-Type": "application/json",
                     "Accept": "application/json",
                     "User-Agent": UA,
                     "Authorization": f"Bearer {os.environ['GROQ_API_KEY'].strip()}"})
        for attempt in range(MAX_RETRIES):
            _pace(a_est)
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    as_data = json.loads(r.read())
                break
            except urllib.error.HTTPError as e:
                a_body = e.read().decode()[:250]
                if e.code in RETRY_ON and attempt < MAX_RETRIES - 1:
                    time.sleep(_backoff(attempt))
                    continue
                if e.code == 401:
                    raise RuntimeError(
                        "groq rejected the key. Check GROQ_API_KEY in .env for a "
                        "doubled gsk_ prefix or stray characters.") from None
                if e.code == 429 and "TPM" in a_body:
                    raise RuntimeError(
                        f"groq token-per-minute limit hit even after pacing at "
                        f"{tpm()} TPM. Lower it: export LLM_TPM=4000") from None
                if e.code == 403 and "1010" in a_body:
                    raise RuntimeError(
                        "groq returned Cloudflare error 1010, which blocks the client "
                        "rather than the key. This build sends a normal User-Agent; if "
                        "you still see it, the network is being filtered. Try another "
                        "connection, or switch LLM_PROVIDER in .env.") from None
                if e.code == 404 and "model" in a_body.lower():
                    raise RuntimeError(
                        f"groq does not have {a_model}. Models are retired "
                        f"periodically. Try: export LLM_MODEL=llama-3.1-8b-instant"
                    ) from None
                raise RuntimeError(f"groq {e.code}: {a_body}") from None
        return as_data["choices"][0]["message"]["content"].strip()

    if p == "cerebras":
        import urllib.error
        import urllib.request
        # Same OpenAI-shaped endpoint as groq, and the same reasoning-effort note
        # applies: gpt-oss writes an analysis pass first, and at the default setting
        # that pass eats the budget and the JSON comes back truncated.
        as_payload = {
            "model": a_model, "max_tokens": max_tokens, "temperature": 0,
            "messages": [{"role": "user", "content": as_prompt}],
        }
        if "gpt-oss" in a_model or "reason" in a_model:
            as_payload["reasoning_effort"] = "low"
        as_body = json.dumps(as_payload).encode()
        req = urllib.request.Request(
            "https://api.cerebras.ai/v1/chat/completions", data=as_body,
            headers={"Content-Type": "application/json",
                     "Accept": "application/json",
                     "User-Agent": UA,
                     "Authorization": f"Bearer {os.environ['CEREBRAS_API_KEY'].strip()}"})
        for attempt in range(MAX_RETRIES):
            _pace(a_est)
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    as_data = json.loads(r.read())
                break
            except urllib.error.HTTPError as e:
                a_body = e.read().decode()[:250]
                if e.code in RETRY_ON and attempt < MAX_RETRIES - 1:
                    time.sleep(_backoff(attempt))
                    continue
                if e.code == 401:
                    raise RuntimeError(
                        "cerebras rejected the key. Check CEREBRAS_API_KEY in .env "
                        "for stray characters.") from None
                if e.code == 429:
                    raise RuntimeError(
                        f"cerebras rate limit hit even after pacing at {tpm()} TPM and "
                        f"{rpm()} RPM. The free tier is tighter on requests than on "
                        f"tokens, so lower requests first: export LLM_RPM=5") from None
                if e.code == 400 and "context" in a_body.lower():
                    raise RuntimeError(
                        "cerebras free tier caps context at 8192 tokens and this "
                        "clause exceeded it. Long clauses need the paid tier or a "
                        "different provider.") from None
                if e.code == 404 and "model" in a_body.lower():
                    raise RuntimeError(
                        f"cerebras does not have {a_model}. The free lineup rotates. "
                        f"Check: python src/llm.py --list") from None
                raise RuntimeError(f"cerebras {e.code}: {a_body}") from None
        return as_data["choices"][0]["message"]["content"].strip()

    if p == "ollama":
        import urllib.request
        as_body = json.dumps({"model": a_model, "prompt": as_prompt, "stream": False,
                              "options": {"temperature": 0}}).encode()
        req = urllib.request.Request("http://localhost:11434/api/generate", data=as_body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read())["response"].strip()

    raise RuntimeError(f"unknown provider '{p}'")


def _gemini_text(as_data):
    """
    Pull the text out, or return None if there genuinely is not any.

    A candidate can come back with no parts at all: the safety filter blocked it, or
    the token budget was spent before any output was produced. Indexing straight into
    parts raised a bare KeyError that said nothing about which of those happened.
    """
    for cand in as_data.get("candidates") or []:
        as_parts = (cand.get("content") or {}).get("parts") or []
        a_text = "".join(p.get("text", "") for p in as_parts).strip()
        if a_text:
            return a_text
    return None


def _gemini_why(as_data):
    """Best available explanation for an empty response."""
    as_bits = []
    for cand in as_data.get("candidates") or []:
        if cand.get("finishReason"):
            as_bits.append(f"finishReason={cand['finishReason']}")
    a_fb = as_data.get("promptFeedback") or {}
    if a_fb.get("blockReason"):
        as_bits.append(f"blockReason={a_fb['blockReason']}")
    if not as_bits:
        as_bits.append("no candidates returned")

    a_why = ", ".join(as_bits)
    if "MAX_TOKENS" in a_why:
        a_why += ". The budget was spent before any text was produced, which happens "\
                 "when a reasoning model thinks for longer than the limit allows."
    return a_why


def _json_candidates(as_text):
    """
    Every balanced {...} in the text, in order, tracking string state and escapes.

    Returning only the first one was not enough. Reasoning models write prose before
    the answer and that prose contains braces, so the first balanced object was
    something like "{this clause}" rather than the result. All candidates are
    returned so the caller can pick the one that looks like an answer.
    """
    as_out = []
    i, n = 0, len(as_text)
    while i < n:
        if as_text[i] != "{":
            i += 1
            continue

        depth, in_str, esc = 0, False, False
        for j in range(i, n):
            c = as_text[j]
            if esc:
                esc = False
                continue
            if c == "\\":
                esc = True
                continue
            if c == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    as_out.append(as_text[i:j + 1])
                    i = j + 1
                    break
        else:
            as_out.append(as_text[i:])       # never closed, so truncated
            break
    return as_out


def _pick_json(as_text, as_expect=("category", "params")):
    """
    Parse the candidate that looks like the answer.

    An object carrying the keys the caller expects wins over one that merely parses,
    because a stray "{a, b}" in the model's reasoning parses to nothing useful and
    would otherwise be accepted as the result.
    """
    as_parsed = []
    for a_cand in _json_candidates(as_text):
        try:
            as_obj = json.loads(a_cand)
        except json.JSONDecodeError:
            as_obj = _repair_json(a_cand)
        if isinstance(as_obj, dict):
            as_parsed.append(as_obj)

    if not as_parsed:
        return None
    for as_obj in as_parsed:
        if any(k in as_obj for k in as_expect):
            return as_obj
    return max(as_parsed, key=len)


def _repair_json(as_text):
    """
    Close a JSON object that was cut off by the token ceiling.

    A truncated reply looks like {"category":"x","params":{"some_key   and the
    regex below finds no closing brace, so the whole clause was discarded. Roughly
    one call in ten came back this way. The complete keys are worth keeping, so
    trim back to the last comma or colon that sits at a safe boundary and close the
    braces that are still open.
    """
    # Cut at the LATEST safe boundary, not the first marker that happens to match.
    # Checking markers in a fixed order and breaking on the first hit cut at the
    # earliest one instead, which threw away every parameter after it and defeated
    # the point of repairing rather than discarding.
    a_best = -1
    for a_mark in ('",', '},', '],', 'true,', 'false,', 'null,'):
        a_at = as_text.rfind(a_mark)
        if a_at > a_best:
            a_best, a_len = a_at, len(a_mark)

    if a_best > 0:
        a_cut = as_text[:a_best + a_len - 1]
    else:
        a_at = max(as_text.rfind('"'), as_text.rfind("}"), as_text.rfind("]"))
        a_cut = as_text[:a_at + 1] if a_at > 0 else as_text

    # a dangling key with no value cannot be kept
    a_cut = re.sub(r',\s*"[^"]*"\s*:?\s*$', "", a_cut)

    a_cut = a_cut.rstrip().rstrip(",")
    n_open = a_cut.count("{") - a_cut.count("}")
    n_arr = a_cut.count("[") - a_cut.count("]")
    if n_open < 0 or n_arr < 0:
        return None
    a_cut += "]" * n_arr + "}" * n_open

    try:
        return json.loads(a_cut)
    except json.JSONDecodeError:
        return None


def complete_json(as_prompt, max_tokens=800):
    """
    Same call, but insist on a JSON object coming back.

    Smaller free-tier models wrap JSON in prose or fences far more often than the
    paid ones do, so the fence strip and object extraction happen here rather than
    in every caller. A truncated reply is repaired rather than discarded.
    """
    as_raw = complete(as_prompt, max_tokens)
    as_text = as_raw.replace("```json", "").replace("```", "").strip()

    as_obj = _pick_json(as_text)
    if as_obj is not None:
        return as_obj

    # One retry with a blunter instruction. Smaller models drift into prose, and a
    # single reminder recovers most of them for the cost of one extra call.
    as_retry = complete(
        as_prompt + "\n\nIMPORTANT: reply with the JSON object and nothing else. "
                    "No explanation, no preamble, no code fences.",
        max_tokens)
    as_retry = as_retry.replace("```json", "").replace("```", "").strip()
    as_obj = _pick_json(as_retry)
    if as_obj is not None:
        return as_obj

    raise ValueError(f"no usable JSON after a retry. First reply began: {as_raw[:200]}")


def list_models():
    """What this key can actually reach. The authoritative answer, not a guess."""
    import urllib.error
    import urllib.request

    if provider() == "cerebras":
        import urllib.error
        import urllib.request
        req = urllib.request.Request(
            "https://api.cerebras.ai/v1/models",
            headers={"User-Agent": UA,
                     "Authorization": f"Bearer {os.environ['CEREBRAS_API_KEY'].strip()}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return [m["id"] for m in json.loads(r.read()).get("data", [])]
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"cerebras {e.code}: {e.read().decode()[:200]}") from None

    if provider() == "groq":
        a_key = os.environ.get("GROQ_API_KEY", "").strip()
        if not a_key:
            raise RuntimeError("GROQ_API_KEY is not set")
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {a_key}", "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return sorted(m["id"] for m in json.loads(r.read()).get("data", []))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"groq {e.code}: {e.read().decode()[:200]}") from None

    if provider() != "gemini":
        return []

    import urllib.error
    import urllib.request

    a_key = os.environ.get("GEMINI_API_KEY", "")
    if not a_key:
        raise RuntimeError("GEMINI_API_KEY is not set")

    a_url = "https://generativelanguage.googleapis.com/v1beta/models?key=" + a_key
    try:
        with urllib.request.urlopen(a_url, timeout=30) as r:
            as_data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            a_hint = ""
            if a_key.count("AQ.") > 1 or a_key.count("AIza") > 1:
                a_hint = (" The key looks doubled: it contains its prefix more than "
                          "once, which usually means part of it was typed and part "
                          "pasted.")
            raise RuntimeError(
                f"gemini rejected the key ({e.code}).{a_hint} Regenerate it at "
                f"aistudio.google.com and set it again.") from None
        raise RuntimeError(f"gemini {e.code}: {e.read().decode()[:180]}") from None

    return [m["name"].split("/")[-1] for m in as_data.get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])]


if __name__ == "__main__":
    import sys
    if "--list" in sys.argv:
        try:
            for m in list_models():
                print(" ", m)
        except RuntimeError as e:
            print(f"  {e}")
        raise SystemExit

    as_clash = env_conflicts()
    if as_clash:
        print("WARNING")
        for k in as_clash:
            print(f"  {k} is set in your shell AND in .env, and the two differ.")
            print(f"  The shell value is being used. If that is the stale one, run:")
            print(f"      unset {k}")
        print()

    ok, why = available()
    a_key = os.environ.get(KEY_ENV.get(provider()) or "", "")
    if a_key:
        a_src = ENV_SOURCE.get(KEY_ENV.get(provider()) or "", "shell export")
        print(f"key:      {a_key[:6]}...{a_key[-4:]}  ({len(a_key)} chars, from {a_src})")
    print(f"provider: {provider()}")
    print(f"model:    {model_name()}")
    print(f"pacing:   {rpm()} calls/min, {tpm()} tokens/min "
          f"(whichever binds first)")
    print(f"ready:    {ok}  ({why})")
    if ok:
        print("\ntest call...")
        try:
            print(" ", complete("Reply with exactly: OK", max_tokens=256))
        except RuntimeError as e:
            print(f"  failed: {e}")
