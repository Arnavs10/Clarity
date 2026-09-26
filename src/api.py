"""
FastAPI backend. Serves the web app and the browser extension from one place.

    uvicorn src.api:app --reload --port 8000

Endpoints:
    GET  /health          liveness plus what is loaded
    GET  /doc-types       the configured document types
    POST /audit           text in, findings out
    POST /audit/upload    file in, findings out
    POST /draft           findings in, negotiation message out

Contract text is never logged or persisted. People paste employment terms into this,
and storing them is a liability with no upside.
"""

import os
import threading
import sys
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fastapi import FastAPI, File, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import segment                          # noqa: E402
import docscan                          # noqa: E402
import doctype_guard                    # noqa: E402
from agent import ClauseAgent           # noqa: E402
from typeregistry import TypeRegistry   # noqa: E402
from negotiate import draft_offline     # noqa: E402

app = FastAPI(title="Clarity", version="1.0.0")

# The extension runs from a chrome-extension:// origin, so it cannot be whitelisted
# by URL. Narrow this to your deployed domain before going public.
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["GET", "POST"], allow_headers=["*"])

_agent: Optional[ClauseAgent] = None
_registry = TypeRegistry()

WEB_DIR = ROOT / "web"

# The public demo. Version comparison keeps the drafts it compares, so it is off.
# Live audits are off as well unless CLARITY_PUBLIC_LIVE=1: the free model allowance
# covers a few documents a day at several minutes each, while a recorded sample of
# the same pipeline opens at once and spends nothing. See web/samples/.
PUBLIC = os.environ.get("CLARITY_PUBLIC") == "1"
PUBLIC_LIVE = os.environ.get("CLARITY_PUBLIC_LIVE") == "1"
REPO_URL = os.environ.get("CLARITY_REPO_URL", "")
if not REPO_URL.startswith(("https://", "http://")):
    REPO_URL = ""

# One live audit at a time on the demo. Calls share one per-minute budget, so a
# second audit doubles both waits instead of running alongside the first.
_live_lock = threading.Lock()

app.mount("/samples", StaticFiles(directory=str(WEB_DIR / "samples"), check_dir=False),
          name="samples")


def _live_gate():
    if PUBLIC and not PUBLIC_LIVE:
        raise HTTPException(403, "Live audits are switched off on this public demo. "
                                 "Open a sample to see a recorded audit, or run Clarity "
                                 "on your own machine to audit your contract.")


def _versions_gate():
    if PUBLIC:
        raise HTTPException(403, "Version comparison stores the drafts it compares, so "
                                 "it is switched off on the public demo. Run Clarity "
                                 "locally to use it.")


def _serially(fn, *as_args):
    if not PUBLIC:
        return fn(*as_args)
    if not _live_lock.acquire(blocking=False):
        raise HTTPException(429, "Another audit is already running on this demo, and each "
                                 "takes several minutes. Try again shortly, or open a sample.")
    try:
        return fn(*as_args)
    finally:
        _live_lock.release()

# How many clauses one request may audit. Each clause is a model call, paced
# against a per-minute token limit, so this is a time and quota ceiling rather
# than a technical one. Raise it if you are on a paid tier.
# Raised from 40 on 21 September 2026. The old ceiling was set against a segmenter
# that was silently merging sections away. Once "13. Notices" and "17. Governing
# Law" stopped vanishing into the clause above them, a real ten-page internship
# letter came to 48 clauses, and a 40 ceiling would have refused a document that is
# entirely ordinary. Triage still skips boilerplate before any model call, so the
# real cost is the clauses that survive triage, not this number.
MAX_CLAUSES = int(os.getenv("CLARITY_MAX_CLAUSES", "60"))


def agent() -> ClauseAgent:
    global _agent
    if _agent is None:
        as_extractor = None
        try:
            from extract import Extractor
            as_extractor = Extractor()          # raises if no provider is configured
        except Exception:
            pass
        _agent = ClauseAgent(extractor=as_extractor)
    return _agent


class AuditRequest(BaseModel):
    text: str
    doc_type: str = "employment"


class Finding(BaseModel):
    heading: Optional[str] = None
    clause_text: Optional[str] = None
    rule_id: str
    category: str
    basis: str
    # The Act and section this finding rests on. The rule engine has always emitted
    # it, but this model did not declare it, and the two construction sites below
    # copy only the fields this model declares. So it was dropped on the way out.
    #
    # That mattered in one place: the browser posts these findings back to /draft,
    # and negotiate.legal_sentence() builds its one legal sentence from source.act
    # and source.section. With source gone it fell through to its norm-tier fallback,
    # so every statutory and regulatory finding was written into the negotiation
    # message as "unusual compared with similar agreements, rather than a breach of
    # law". The cards kept the four tiers apart and the draft quietly flattened them,
    # in the one artefact a person sends to an employer under their own name.
    #
    # It also left validate_draft's allowed-sections check with an empty set to check
    # against, so that check was passing on everything.
    source: Optional[dict] = None
    # True when every field the rule read was taken from the clause text by
    # pattern rather than from the model, so the finding is the same on every run.
    from_text: bool = False
    evidence: Optional[list] = None
    severity: str
    risky: bool
    message: str
    note: Optional[str] = None
    citation_source: Optional[str] = None
    citation_span: Optional[str] = None
    verified: bool = False


class ClauseResult(BaseModel):
    index: int
    heading: Optional[str]
    text: str
    triage_keep: bool
    # A clause the model never managed to read. Distinct from triage_keep, which
    # means deliberately skipped as low risk. Without this the interface cannot tell
    # "read and clean" from "never read", and renders both as green.
    extraction_failed: bool = False
    findings: List[Finding]
    reassurances: List[Finding] = []


class AuditResponse(BaseModel):
    doc_type: str
    clauses: int
    flagged: int
    skipped_by_triage: int
    high: int
    results: List[ClauseResult]
    # Without an extraction backend no parameters are pulled from the clauses, so no
    # rule can fire and every document comes back with zero findings. A reader would
    # take that as "my contract is clean", which is the worst possible thing for this
    # tool to imply. The flag lets the interface say "not analysed" instead.
    extraction_available: bool = True
    # How many clauses the model failed to read. The interface used to source its
    # "not analysed" tile from the triage count, so a document where extraction
    # failed on every clause reported zero not-analysed and zero findings, which
    # reads as a clean contract.
    # What the rule set actually looked for. Without it, zero findings is a shrug:
    # a reader cannot tell a document that passed from a document nobody checked
    # properly, and has no way to see what is outside the tool's scope. With it,
    # silence is a claim the reader can audit.
    checks: List[str] = []
    unread: int = 0
    # Whether the document looks like the type it was read as.
    #
    # Nothing checked this. An internship offer letter uploaded as a loan agreement
    # was read by the loan rules, produced a high-severity finding about calling in
    # the entire balance, and attached it to the clause where the candidate confirms
    # his degree. The reader was given no signal at all that anything was wrong.
    #
    # ok | mismatch | unrecognised | too_short | unknown_type
    type_status: str = "ok"
    type_suggested: Optional[str] = None
    type_message: Optional[str] = None
    # The draft is the one output that leaves this tool and reaches another person.
    # A wrong finding on screen costs the reader a minute; a wrong demand sent to a
    # landlord or a lender costs them their credibility. So when the document does
    # not look like the type it was read as, the findings are still shown, with the
    # warning above, and the draft is withheld.
    draft_allowed: bool = True
    warning: Optional[str] = None
    disclaimer: str = "Flags for review, not legal advice."


def as_finding(f):
    """
    Build a Finding from a rule-engine dict, taking only the keys it actually has.

    The old form was {k: f.get(k) for k in Finding.model_fields}, which turns every
    absent key into an explicit None. That worked while every field on this model
    was either always present or Optional. Adding from_text: bool broke it the
    moment a finding did not carry the key, which is most of them, and every upload
    returned a 500 with a pydantic error about None not being a boolean.
    Passing only the keys that exist lets each field fall back to its own default,
    so a new non-optional field can never do this again.
    """
    return Finding(**{k: v for k, v in f.items() if k in Finding.model_fields})


def _run(text: str, doc_type: str) -> AuditResponse:
    if doc_type not in _registry.slugs():
        raise HTTPException(400, f"unknown doc_type. known: {_registry.slugs()}")
    if not text or len(text.strip()) < 100:
        raise HTTPException(400, "text too short to be a contract")

    # Does the document look like the type it was read as. Deterministic, so it
    # cannot vary between runs, and advisory, so an unusual but genuine contract
    # is never refused outright. See doctype_guard.py.
    as_labels = {s_: _registry.get(s_)["label"] for s_ in _registry.slugs()}
    a_type = doctype_guard.check(text, doc_type, as_labels=as_labels)

    # Facts about the whole agreement, read once by pattern before any clause is
    # examined, so a rule that claims something is true of the document is not
    # deciding it from one clause. See docscan.py.
    as_doc_params, _as_doc_evidence = docscan.read_document(doc_type, text)

    # Segmented in memory. This used to round-trip through a temporary file and
    # segment.run(), which also saved every clause to data/clauses, so each audited
    # contract stayed on disk while the page promised it was never written there.
    as_clauses, _how, _n = segment.split_text(text, doc_type)

    if not as_clauses:
        raise HTTPException(422, "could not segment this document into clauses")

    # A ceiling on how much work one request may start.
    #
    # There was none, so a ten page letter segmented into dozens of clauses and the
    # server began a model call for each, paced against a per-minute token limit.
    # It ran for many minutes with nothing on screen, consumed most of a day's free
    # quota in one request, and there was no way to tell from the page whether it
    # was working or hung. Refusing up front, with the arithmetic shown, is better
    # than starting work that cannot finish in a reasonable time.
    if len(as_clauses) > MAX_CLAUSES:
        a_mins = max(1, round(len(as_clauses) / 6))
        raise HTTPException(
            413,
            f"This document has {len(as_clauses)} clauses, above the limit of "
            f"{MAX_CLAUSES}. Reading them all would take roughly {a_mins} minutes "
            f"against the current rate limit and would use most of a day's quota. "
            f"Paste the section you actually want checked, or raise "
            f"CLARITY_MAX_CLAUSES in .env if your provider allows it.")

    as_agent = agent()
    # A short document is read in full. See TRIAGE_MIN_CLAUSES in agent.py.
    from agent import TRIAGE_MIN_CLAUSES
    a_short_doc = len(as_clauses) < TRIAGE_MIN_CLAUSES

    as_results, n_flagged, n_high, n_skipped = [], 0, 0, 0
    as_errors = []
    as_doc_scope_rules = {r["id"] for r in as_agent.engine.rules_for(doc_type)
                          if r.get("scope") == "document"}
    as_doc_scope_seen = set()

    for i, c in enumerate(as_clauses):
        out = as_agent.run(doc_type, c.text, clause_id=c.clause_id,
                           always_extract=a_short_doc, doc_params=as_doc_params)
        a_failed = bool(out.get("extraction_error"))
        if a_failed:
            as_errors.append(out["extraction_error"])

        # A document-scope rule is true of the whole agreement, so it is true of
        # every clause at once. Without this the reader would be told twenty-two
        # separate times that the lease is unregistered. It is reported once, on
        # the first clause that raised it.
        as_findings = []
        for f in (out.get("findings") or []):
            if f.get("rule_id") in as_doc_scope_rules:
                if f["rule_id"] in as_doc_scope_seen:
                    continue
                as_doc_scope_seen.add(f["rule_id"])
            as_findings.append(f)

        if as_findings:
            n_flagged += 1
        n_high += sum(1 for f in as_findings if f["severity"] == "high")
        if not out.get("triage_keep"):
            n_skipped += 1

        as_results.append(ClauseResult(
            index=i, heading=c.heading, text=c.text,
            triage_keep=bool(out.get("triage_keep")),
            extraction_failed=a_failed,
            findings=[as_finding(f) for f in as_findings],
            reassurances=[as_finding(f) for f in (out.get("reassurances") or [])]))

    # This flag answers one question only: is there an extraction backend at all.
    # It used to also go false whenever any single clause failed, which collapsed two
    # different states into one. The interface reads a false here as "nothing in this
    # document was read", so a letter where 2 of 13 clauses failed rendered all 13 as
    # not checked, printed the no-clause-was-read verdict over a warning that said
    # 2 of 13, and still listed a clause it had checked and found acceptable. Six
    # signals on one page, disagreeing.
    #
    # Partiality is carried by `unread` instead, which is a count and cannot lie about
    # its own size. Nothing is lost on the total-failure path: with no backend every
    # clause is unread and this stays false, and when every clause errors unread
    # equals the clause count, which the interface already treats as total.
    as_available = as_agent.extractor is not None
    a_warn = None
    if as_agent.extractor is None:
        import llm
        _, why = llm.available()
        a_warn = ("No extraction backend configured, so no clause was analysed. Zero "
                  f"findings here does not mean the contract is clean. {why}.")
    elif as_errors:
        a_warn = (f"Extraction failed on {len(as_errors)} of {len(as_clauses)} clauses, "
                  f"so those were not checked and this result is incomplete. Zero "
                  f"findings on an unread clause does not mean it is safe. "
                  f"First error: {as_errors[0]}")
    # A type problem outranks an extraction warning. If the document is not what it
    # was read as, nothing below it is worth reporting first.
    if a_type["status"] in ("mismatch", "unrecognised"):
        a_warn = a_type["message"] + (f" {a_warn}" if a_warn else "")

    # On a clear mismatch the findings are withheld, not merely labelled.
    #
    # The topic gates already throw out a fact the model read into a clause that is
    # not about it, and on the offer letter read as a loan they removed three of the
    # four. The fourth was not a misreading at all: the letter really does contain a
    # jurisdiction clause, so a loan rule about jurisdiction fired on it correctly
    # and produced a finding about a lender that does not exist.
    #
    # No gate can fix that, because nothing is wrong at the clause level. The wrong
    # question is being asked of the whole document. A reader shown one plausible
    # finding under a warning will read the finding and skim the warning, so the
    # finding does not survive the mismatch. The clause text stays, so they can
    # still see what was read, and the message tells them what to change.
    if a_type["status"] == "mismatch":
        as_results = [r.model_copy(update={"findings": [], "reassurances": []})
                      for r in as_results]
        n_flagged = n_high = 0

    return AuditResponse(
        doc_type=doc_type, clauses=len(as_clauses), flagged=n_flagged,
        skipped_by_triage=n_skipped, high=n_high, results=as_results,
        extraction_available=as_available, unread=len(as_errors), warning=a_warn,
        type_status=a_type["status"],
        type_suggested=a_type["suggested"],
        type_message=a_type["message"],
        draft_allowed=a_type["status"] not in ("mismatch", "unrecognised"),
        checks=sorted({r["category"].replace("_", " ")
                       for r in as_agent.engine.rules_for(doc_type)}))


@app.get("/", include_in_schema=False)
def root():
    """
    A browser pointed at the API port lands here. Returning a bare 404 looked like
    the server was broken when it was working correctly and simply had no page to
    serve, so it says what it is and where the interface lives.
    """
    a_page = WEB_DIR / "index.html"
    if a_page.exists():
        return FileResponse(a_page)
    return {
        "service": "Clarity API",
        "note": "This is the backend. The web interface is served separately.",
        "interface": "run: python -m http.server 5500 --bind 127.0.0.1 --directory web",
        "endpoints": {
            "GET /health": "what is loaded",
            "GET /doc-types": "configured document types",
            "POST /audit": "{text, doc_type} -> findings",
            "POST /audit/upload": "file upload -> findings",
            "POST /draft": "findings -> negotiation message",
            "POST /versions/save": "store a draft for later comparison",
            "GET /versions/{doc_id}/diff": "what changed between two drafts",
        },
        "docs": "/docs",
        "disclaimer": "Flags for review, not legal advice.",
    }


@app.get("/health")
def health():
    a = agent()
    return {"ok": True,
            "doc_types": _registry.slugs(),
            "rules": {t: len(a.engine.rules_for(t)) for t in _registry.slugs()},
            "statutes_indexed": len(set(c["corpus_id"] for c in a.index.chunks)),
            "router_loaded": a.router is not None,
            "extractor_loaded": a.extractor is not None}


@app.get("/doc-types")
def doc_types():
    return {"types": [{"slug": s, "label": _registry.get(s)["label"],
                       "categories": len(_registry.categories(s))}
                      for s in _registry.slugs()],
            "public": PUBLIC, "live": (not PUBLIC) or PUBLIC_LIVE, "repo": REPO_URL}


@app.post("/audit", response_model=AuditResponse)
def audit(req: AuditRequest):
    _live_gate()
    return _serially(_run, req.text, req.doc_type)


@app.post("/audit/upload", response_model=AuditResponse)
async def audit_upload(file: UploadFile = File(...), doc_type: str = "employment"):
    _live_gate()
    as_bytes = await file.read()
    a_suffix = Path(file.filename or "x.txt").suffix.lower()

    if a_suffix in (".pdf", ".docx"):
        # Parsed from memory, so an upload never touches the disk.
        # A missing optional dependency is a configuration problem, not a server
        # fault. A 500 traceback taught the reader nothing and looked like the
        # application had crashed when it was simply running under an interpreter
        # that did not have the PDF library installed.
        try:
            text = segment.read_bytes(as_bytes, a_suffix)
        except RuntimeError as e:
            raise HTTPException(400, str(e))
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                400,
                f"Could not read {file.filename}. It may be a scanned image rather "
                f"than text, or the file may be damaged. ({type(e).__name__})")
    else:
        text = as_bytes.decode("utf-8", errors="ignore")

    # _run is fully synchronous and can take minutes: one model call per clause,
    # paced against a rate limit. On an async endpoint that work sits on the event
    # loop and blocks every other request the server has, so while a long document
    # is being audited the page cannot even fetch its list of document types. That
    # is what the spinner that never resolves actually was: not a slow dropdown,
    # a server with no thread free to answer.
    #
    # A sync def endpoint would have gone to the threadpool automatically. This one
    # is async because the upload has to be awaited, so the blocking half is handed
    # to the threadpool explicitly.
    return await run_in_threadpool(_serially, _run, text, doc_type)


class VersionSaveRequest(BaseModel):
    doc_id: str
    version: str
    text: str
    doc_type: str = "employment"


@app.post("/versions/save")
def versions_save(req: VersionSaveRequest):
    """Audit a draft and store it under a document id, for later comparison."""
    _versions_gate()
    import versions as V
    a_res = _run(req.text, req.doc_type)
    as_rows = [{"clause_text": r.text, "heading": r.heading,
                "findings": [f.model_dump() for f in r.findings]}
               for r in a_res.results]
    V.save(req.doc_id, req.version, as_rows, req.doc_type)
    return {"doc_id": req.doc_id, "version": req.version,
            "clauses": a_res.clauses, "flagged": a_res.flagged,
            "versions": V.versions(req.doc_id)}


@app.get("/versions/{doc_id}")
def versions_list(doc_id: str):
    _versions_gate()
    import versions as V
    return {"doc_id": doc_id, "versions": V.versions(doc_id)}


@app.get("/versions/{doc_id}/diff")
def versions_diff(doc_id: str, from_version: str, to_version: str):
    """
    What changed between two drafts.

    The status worth reading is REWORDED: the clause was edited and the finding
    survived, which is an edit that looks like a fix and is not one.
    """
    _versions_gate()
    import versions as V
    try:
        return V.diff(doc_id, from_version, to_version)
    except SystemExit as e:
        raise HTTPException(404, str(e))


class DraftRequest(BaseModel):
    doc_type: str
    findings: List[dict]
    tone: Optional[str] = "firm"


@app.post("/draft")
def draft(req: DraftRequest):
    """
    Model-written where possible, template where not.

    The template was the only path being used, so every contract produced the same
    letter with the rule messages swapped in. A negotiation message that does not
    mention what this contract actually says is not worth sending.
    """
    _live_gate()
    a_source = "template"
    try:
        from negotiate import draft_message
        text = draft_message(req.doc_type, req.findings, tone=req.tone or "firm")
        a_source = "model"
    except Exception:
        text = None

    if not text:
        text = draft_offline(req.doc_type, req.findings)

    if not text:
        raise HTTPException(422, "no verified risky findings to draft from")
    return {"draft": text, "written_by": a_source,
            "note": "Read it, edit it, send it yourself. Nothing is sent for you."}
