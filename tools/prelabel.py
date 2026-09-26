"""
Pre-annotates the dev split so review is a click instead of a reading exercise.

    export ANTHROPIC_API_KEY=sk-ant-...
    python tools/prelabel.py --type employment

Only touches clauses marked split=dev. The blind test split is never sent to a
model, because those are the labels the reported precision and recall are computed
on, and a suggestion would anchor the human who reviews them.

Suggestions are not labels. Nothing here writes to data/labels/. The labeler shows
each suggestion pre-selected, you accept or change it, and the label it saves
records whether you changed it. That correction rate is the honest measure of how
much the assistance was worth.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from schema import read_jsonl, write_jsonl, SEVERITIES   # noqa: E402
from typeregistry import TypeRegistry                    # noqa: E402

QUEUE_DIR = ROOT / "data" / "queue"
SUGGEST_DIR = ROOT / "data" / "suggestions"

MODEL = "claude-sonnet-4-6"

MECHANISMS = """A clause is risky ONLY if you can name the mechanism. The six are:
1. Money leaves the person (forfeiture, penalty, bond, clawback, deposit withheld)
2. Freedom restricted AFTER they leave (non-compete, non-solicit, long garden leave)
3. Ownership taken (IP assignment beyond real work product, no prior-work carve-out)
4. Asymmetry (one side gets shorter notice, easier exit, picks the arbitrator)
5. Unbounded discretion ("sole discretion", "at any time", "as determined by the Company")
6. No limit where one belongs (perpetual, unlimited, no cure period, no cap)

NOT risky, regardless of length or density: definitions, recitals, signature blocks,
procedure, anything binding both sides equally, anything the employer gives (salary,
insurance, reimbursement), dates, durations, fragments that end mid-sentence.

Length is not risk. Default to not risky when unsure."""


def build_prompt(as_profile, clause_text):
    cats = []
    for c in as_profile["taxonomy"]:
        line = f"- {c['id']}: {c.get('desc','')}"
        if c.get("watch"):
            line += f"  [watch for: {c['watch']}]"
        cats.append(line)

    anchors = "\n".join(f"- {a}" for a in as_profile.get("anchors", []))

    return f"""You are pre-annotating contract clauses for a risk-flagging tool grounded in Indian law.

CATEGORIES (pick exactly one):
{chr(10).join(cats)}

LEGAL ANCHORS:
{anchors}

{MECHANISMS}

CLAUSE:
\"\"\"{clause_text[:4000]}\"\"\"

Respond with ONLY a JSON object, no preamble and no markdown fences:
{{"category": "<one id from the list>", "risky": true|false, "severity": "none|low|medium|high", "rationale": "<max 12 words naming the mechanism, empty string if not risky>", "confidence": 0.0-1.0}}

Rules: severity must be "none" when risky is false, and never "none" when risky is true.
rationale must be non-empty when risky is true. Set confidence below 0.6 when the
clause is a fragment or you are genuinely unsure."""


def call(client, as_prompt):
    r = client.messages.create(
        model=MODEL,
        max_tokens=300,
        messages=[{"role": "user", "content": as_prompt}],
    )
    text = "".join(b.text for b in r.content if b.type == "text").strip()
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)


def coerce(as_sugg, valid_cats):
    """Fix anything the model got structurally wrong rather than trusting it blindly."""
    if as_sugg.get("category") not in valid_cats:
        as_sugg["category"] = "boilerplate"
        as_sugg["confidence"] = 0.3

    if as_sugg.get("severity") not in SEVERITIES:
        as_sugg["severity"] = "none"

    risky = bool(as_sugg.get("risky"))
    if risky and as_sugg["severity"] == "none":
        as_sugg["severity"] = "medium"
    if not risky:
        as_sugg["severity"] = "none"
        as_sugg["rationale"] = ""
    if risky and not (as_sugg.get("rationale") or "").strip():
        as_sugg["rationale"] = "review needed"
        as_sugg["confidence"] = 0.3

    as_sugg["risky"] = risky
    return as_sugg


def run(doc_type, limit=None):
    try:
        from anthropic import Anthropic
    except ImportError:
        print("pip install anthropic")
        sys.exit(1)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("set ANTHROPIC_API_KEY first:\n  export ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)

    a_queue = QUEUE_DIR / f"{doc_type}.jsonl"
    if not a_queue.exists():
        print(f"no queue for {doc_type}. Run tools/build_queue.py first")
        sys.exit(1)

    rows = [r for r in read_jsonl(a_queue) if r.get("split") == "dev"]
    if limit:
        rows = rows[:limit]
    if not rows:
        print(f"no dev-split clauses for {doc_type}. Rebuild the queue with the current build_queue.py")
        sys.exit(1)

    as_reg = TypeRegistry()
    as_profile = as_reg.get(doc_type)
    valid = set(as_reg.categories(doc_type))
    client = Anthropic()

    SUGGEST_DIR.mkdir(parents=True, exist_ok=True)
    out_path = SUGGEST_DIR / f"{doc_type}.jsonl"

    done = {}
    if out_path.exists():
        done = {r["clause_id"]: r for r in read_jsonl(out_path)}
        print(f"resuming, {len(done)} already suggested")

    out, n_risky, n_low_conf = list(done.values()), 0, 0
    print(f"pre-annotating {len(rows)} dev clauses for {doc_type}\n")

    for i, r in enumerate(rows, 1):
        if r["clause_id"] in done:
            continue
        try:
            as_sugg = coerce(call(client, build_prompt(as_profile, r["text"])), valid)
        except Exception as e:
            print(f"  [{i}] failed ({e}), skipping")
            continue

        as_sugg["clause_id"] = r["clause_id"]
        out.append(as_sugg)
        n_risky += bool(as_sugg["risky"])
        n_low_conf += as_sugg.get("confidence", 1) < 0.6

        flag = "RISKY" if as_sugg["risky"] else "     "
        print(f"  [{i:>3}/{len(rows)}] {flag} {as_sugg['category']}")

        if i % 10 == 0:
            write_jsonl(out_path, out)
        time.sleep(0.2)

    write_jsonl(out_path, out)
    print(f"\n{len(out)} suggestions -> {out_path.relative_to(ROOT)}")
    print(f"  {n_risky} flagged risky ({n_risky/max(len(out),1):.0%})")
    print(f"  {n_low_conf} low confidence, those need a proper read")
    print("\nnow review them:  streamlit run tools/labeler.py")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--type", required=True)
    ap.add_argument("--limit", type=int, default=None)
    a_args = ap.parse_args()
    run(a_args.type, a_args.limit)
