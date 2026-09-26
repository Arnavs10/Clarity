"""
The agent loop.

    python src/agent.py --demo employment
    python src/agent.py data/raw/employment_01.txt employment --limit 5

Graph shape:

    triage -> plan -> act -> assess -> verify -+-> aggregate
                ^                              |
                +---------- retry -------------+

triage     cheap classifier decides whether a clause is worth the expensive path
plan       picks which tools this clause needs, rather than running a fixed script
act        calls them through the MCP tool layer
assess     extracts parameters and runs the rule engine
verify     checks every finding's citation actually supports it; unsupported
           findings go back to plan with a note, up to MAX_RETRIES
aggregate  assembles what survived

The verify node is the point of the whole thing. A finding whose citation does not
contain the section it claims never reaches the user, which is what stops the system
inventing law that sounds plausible.
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Annotated, TypedDict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from langgraph.graph import StateGraph, END   # noqa: E402
from rules import RuleEngine                  # noqa: E402
from retrieve import StatuteIndex             # noqa: E402
import segment                                # noqa: E402
from deterministic import read_facts, apply_gates, DETERMINISTIC_ONLY   # noqa: E402

MAX_RETRIES = 2

# Unmistakable risk language. A clause containing any of these reaches extraction
# regardless of what the triage classifier scores it, because the cost of one extra
# model call is a fraction of a rupee and the cost of missing one of these is the
# whole point of the tool.
SAFETY_KEEP = [
    "minimum period", "minimum service", "liquidated damages", "shall stand forfeited",
    "non-compete", "non compete", "restraint of trade", "shall not compete",
    "restricted period", "sole discretion", "sole opinion", "in perpetuity",
    "irrevocably assign", "outside the usual hours", "outside normal working",
    "off the premises", "moral rights", "clawback", "penal interest",
    "without notice", "immediate effect", "set off", "personal guarantee",
    "shall be forfeited", "bond amount", "service bond",
    # Notice terms. Asymmetric notice is one of the commonest real unfair terms and
    # is invisible to a classifier trained on longer commercial agreements, where it
    # is phrased differently.
    "days notice", "days written notice", "notice of resignation", "notice period",
    "months notice", "terminate this", "may terminate",
]

# Below this many clauses, triage is switched off entirely.
#
# Triage exists to avoid paying for a model call on a clause that cannot matter. On a
# five hundred clause commercial agreement that is most of the cost. On a nine clause
# offer letter it saves a few pennies and risks the answer: a classifier trained on
# long commercial text scores short Indian offer letter clauses low, and four of nine
# clauses were being dropped before anything read them. The saving is not worth the
# recall on a document a person could read themselves in ten minutes.
TRIAGE_MIN_CLAUSES = 25


class AuditState(TypedDict, total=False):
    doc_type: str
    clause_text: str
    clause_id: str
    always_extract: bool
    triage_keep: bool
    triage_reason: str
    plan: list
    params: dict
    # For facts read from the text by pattern: the words each one matched, so a
    # deterministic fact can always show where it came from.
    evidence: dict
    # Facts about the whole document, read by pattern before any clause was seen.
    # Prefixed doc__ so a rule opts into document scope visibly and a document
    # fact can never shadow a clause fact. See docscan.py.
    doc_params: dict
    category: str
    confidence: float
    findings: list
    rejected: list
    retries: int
    notes: list
    reassurances: list
    extraction_error: str
    gated_out: list
    trace: Annotated[list, lambda a, b: (a or []) + (b or [])]


# A retrieved span shorter than this is a heading or an index entry, not the
# provision itself. Set from the shortest genuine section in the statute corpus.
# Lowered from 180 after it withheld legitimate findings on short real sections.
# Section 27 of the Contract Act is genuinely brief, and a threshold tuned on a
# long section rejected it. A heading is under a hundred characters; a provision,
# even a short one, is not.
MIN_SPAN_CHARS = 120


def _normalise_statute(text):
    """
    Flatten the quirks of a bare Act PDF before matching against it.

    India Code text carries amendment markers such as 1[(1)] and 2[***], soft
    hyphens where a word broke across a line, and runs of whitespace from the page
    layout. An exact phrase match against raw text fails on all three, which meant
    a span that plainly contained the words the rule relied on was rejected as if
    it did not.
    """
    a_t = re.sub(r"\d+\[|\]|\*+", " ", text)
    a_t = a_t.replace("-\n", "").replace("\u00ad", "")
    a_t = re.sub(r"[^\w\s]", " ", a_t)
    return re.sub(r"\s+", " ", a_t).strip().lower()


def _anchor_present(span, as_anchors):
    """
    An anchor matches when every significant word in it appears in the span.

    Phrase matching was too literal: "reasonable compensation" fails against
    "reasonable compensation not exceeding" only if a stray marker lands between
    the two words, which in a scanned Act is common. Requiring the words rather
    than the exact string keeps the check honest while surviving the formatting.
    """
    a_span = _normalise_statute(span)
    for a in as_anchors:
        as_words = [w for w in _normalise_statute(a).split() if len(w) > 2]
        if as_words and all(w in a_span for w in as_words):
            return True
    return False


class ClauseAgent:
    """
    Holds the graph and the tools. The tools are the same callables the MCP server
    exposes, so the agent and an external MCP client exercise identical code.
    """

    def __init__(self, extractor=None, router=None):
        self.engine = RuleEngine()
        self.index = StatuteIndex()
        if not self.index.chunks:
            self.index.build()
        self.extractor = extractor

        # Load the trained router unless one was passed in. Without this the agent
        # silently kept using the keyword fallback even after training, so the
        # measured skip rate never matched the benchmark.
        if router is None:
            try:
                from router import TriageRouter
                router = TriageRouter.load()
            except Exception:
                router = None
        self.router = router
        self.graph = self._build()

    # ---------- tools ----------

    def tool_lookup_statute(self, corpus_id, section):
        return self.index.by_section(self.index.resolve_id(corpus_id) or corpus_id, section)

    def tool_search_corpus(self, query, k=3):
        return self.index.by_query(query, k=k)

    def tool_list_rules(self, doc_type):
        return [{"id": r["id"], "category": r["category"], "basis": r["basis"]}
                for r in self.engine.rules_for(doc_type)]

    def tool_evaluate_rules(self, doc_type, as_params):
        return [f.to_dict() for f in self.engine.evaluate(doc_type, as_params)]

    def tool_required_params(self, doc_type):
        return self.engine.required_params(doc_type)

    # ---------- nodes ----------

    def _triage(self, state):
        """
        Cheap gate. Boilerplate never reaches an LLM call.

        Skipped entirely when parameters were supplied by the caller: triage exists
        to decide whether an extraction call is worth making, so with nothing to
        save there is nothing to gate. Without this the trained router silently
        suppressed clauses that had already been extracted.
        """
        if state.get("params") is not None:
            return {"triage_keep": True, "triage_reason": "params supplied, no call to save",
                    "trace": ["triage: bypassed, parameters already supplied"]}

        if state.get("always_extract"):
            return {"triage_keep": True, "triage_reason": "short document, triage off",
                    "trace": ["triage: off, document is short enough to read in full"]}

        text = state["clause_text"]

        # Phrases that must never be skipped, whatever the classifier says.
        #
        # The router is a cost optimisation trained on one corpus, and it will score
        # an unfamiliar phrasing low. A service bond written as "you shall serve for
        # a minimum period of three years" was dropped before extraction because the
        # training corpus phrased bonds differently, and the finding was then missing
        # for a reason no rule or model could explain. A classifier may decide what
        # is probably boring. It should not get to decide that an unmistakable phrase
        # is boring.
        a_low = text.lower()
        for a_phrase in SAFETY_KEEP:
            if a_phrase in a_low:
                return {"triage_keep": True,
                        "triage_reason": f"safety net matched '{a_phrase}'",
                        "trace": [f"triage: kept by safety net ('{a_phrase}')"]}

        if self.router is not None:
            keep, reason = self.router.keep(text)
        else:
            # Fallback heuristic, deliberately recall-first: default to keeping and
            # skip only what is clearly inert. An earlier version listed risk words
            # and kept a clause only if one matched, which silently dropped a service
            # bond clause because it said "shall pay a sum" rather than "penalty".
            # Sending a dull clause to the model costs a fraction of a rupee. Missing
            # a bond costs the user the bond.
            low = text.lower()

            as_inert = ["capitalized terms", "shall have the meanings", "in witness",
                        "counterparts", "severability", "headings are for convenience",
                        "entire agreement", "notices shall be sent to", "signature",
                        "witnesseth", "recitals", "table of contents", "exhibit ",
                        "definitions"]
            as_risky = ["shall not", "restrict", "terminat", "penalty", "forfeit",
                        "assign", "invention", "deposit", "notice", "compet",
                        "solicit", "confidential", "indemnif", "waive", "discretion",
                        "shall pay", "sum of", "bond", "minimum of", "liquidated",
                        "arbitrat", "jurisdiction", "escalat", "renew", "sublet",
                        "lock-in", "lock in", "clawback", "recover", "damages",
                        "breach", "obligat", "covenant"]

            n_risk = sum(1 for w in as_risky if w in low)
            n_inert = sum(1 for w in as_inert if w in low)

            # Length is not a proxy for risk. "The entire security deposit shall
            # stand forfeited" is fifty characters and is the worst clause in a
            # lease, so a short clause is only skipped when it also says nothing
            # risky. A length gate alone dropped a service bond that happened to
            # be two characters under the cut.
            if n_inert >= 2 and n_risk <= 1:
                keep, reason = False, f"{n_inert} boilerplate markers outweigh {n_risk} risk word"
            elif n_risk >= 1:
                keep, reason = True, f"{n_risk} risk signals"
            elif n_inert > 0:
                keep, reason = False, f"inert, {n_inert} boilerplate markers, no risk words"
            elif len(text) < 150:
                keep, reason = False, "short and no risk signals"
            else:
                keep, reason = True, "no clear signal either way, keeping"

        return {"triage_keep": keep, "triage_reason": reason,
                "trace": [f"triage: {'keep' if keep else 'skip'} ({reason})"]}

    def _plan(self, state):
        """
        Decide what this clause needs. On a retry, narrow the plan to whatever the
        verifier complained about instead of repeating the same work.
        """
        doc_type = state["doc_type"]
        as_notes = state.get("notes") or []

        if as_notes:
            as_plan = ["re_extract", "evaluate_rules", "cite"]
            why = f"retry {state.get('retries', 0)}: {as_notes[-1]}"
        else:
            as_plan = ["extract_params", "evaluate_rules", "cite"]
            why = f"{len(self.engine.rules_for(doc_type))} rules in scope"

        return {"plan": as_plan, "trace": [f"plan: {' -> '.join(as_plan)} ({why})"]}

    def _act(self, state):
        """
        Extraction. The model reads; it never decides risk.

        A failed call degrades to zero parameters instead of raising. One bad API
        key used to take down the whole request with a 500, which is the wrong
        failure: the caller cannot tell an unreadable clause from a broken backend.
        The trace records what happened so the difference stays visible.

        A small set of facts is read from the text by pattern before the model is
        asked, and those win. Extraction is sampled, so the same offer letter could
        come back clean on one run and carry a high-severity flag on the next, with
        nothing changed but the call. For a clause that costs ten lakh rupees that
        is not a tolerable way to decide. See deterministic.py for which facts and
        why the list is short.
        """
        a_fixed, a_evidence = read_facts(state["doc_type"], state["clause_text"])

        if self.extractor is None:
            as_params = dict(state.get("params") or {})
            as_params, as_gated = apply_gates(state["doc_type"],
                                              state["clause_text"], as_params)
            as_params.update(a_fixed)
            as_params.update(state.get("doc_params") or {})
            return {"params": as_params, "evidence": a_evidence,
                    "trace": [f"act: no extractor, using supplied params "
                              f"plus {len(a_fixed)} read from the text"
                              + (f", {len(as_gated)} off-topic dropped"
                                 if as_gated else "")]}

        try:
            out = self.extractor.extract(state["doc_type"], state["clause_text"])
        except Exception as e:
            # Even with the provider down, a fact read from the text is still a
            # fact. The clause is still reported as unread, because the model saw
            # none of it, but a bond the pattern found is not thrown away.
            as_params = dict(a_fixed)
            as_params.update(state.get("doc_params") or {})
            return {"params": as_params, "evidence": a_evidence, "confidence": 0.0,
                    "extraction_error": str(e)[:200],
                    "trace": [f"act: extraction failed ({type(e).__name__}), "
                              f"{len(a_fixed)} params read from the text"]}

        as_params = dict(out.get("params", {}))
        # Drop anything the model volunteered for a field the reader owns. A
        # hallucinated weekly_hours fires a real rule on a number that appears
        # nowhere in the document, and it would look identical to a real finding.
        a_dropped = [k for k in list(as_params) if k in DETERMINISTIC_ONLY]
        for k in a_dropped:
            as_params.pop(k)
        # A fact is only trusted about a clause that is on that subject. The model
        # returned acceleration_without_notice for a clause in which a candidate
        # confirms his degree, and a rule cannot tell that from a real one.
        as_params, as_gated = apply_gates(state["doc_type"],
                                          state["clause_text"], as_params)

        a_overrode = [k for k in a_fixed if as_params.get(k) != a_fixed[k]]
        as_params.update(a_fixed)

        # Document facts go on last. Nothing can shadow them and they shadow
        # nothing, because of the doc__ prefix.
        as_params.update(state.get("doc_params") or {})

        return {"params": as_params,
                "evidence": a_evidence,
                "category": out.get("category"),
                "confidence": out.get("confidence", 0.0),
                "gated_out": [k for k, _ in as_gated],
                "trace": [f"act: extracted {len(out.get('params', {}))} params, "
                          f"category {out.get('category')}"
                          + (f", {len(a_overrode)} read from the text instead"
                             if a_overrode else "")
                          + (f", {len(a_dropped)} discarded as reader-owned"
                             if a_dropped else "")
                          + (f", dropped as off-topic for this clause: "
                             f"{', '.join(k for k, _ in as_gated)}"
                             if as_gated else "")]}

    def _assess(self, state):
        """Rules fire here, and only here."""
        as_findings = self.engine.evaluate(state["doc_type"], state.get("params") or {})
        a_evidence = state.get("evidence") or {}
        out = []
        for f in as_findings:
            # The rule's anchors go in, so retrieval widens the window until the
            # span contains the words the finding rests on. Without this the
            # verifier could only reject a truncated citation, never repair it.
            hit = (self.index.cite(f.source, anchors=f.anchors)
                   if f.source else None)
            if hit:
                f.citation_source = hit["corpus_id"]
                f.citation_span = hit["text"]
            d = f.to_dict()
            # If every field this rule reads was read off the text by pattern, the
            # finding did not depend on the model noticing anything, and it will be
            # the same on every run. Worth saying out loud: a reader who has seen
            # the same contract come back differently twice has no reason to trust
            # a third answer unless the interface distinguishes the two kinds.
            as_fields = self.engine.params_for(f.rule_id, state["doc_type"])
            # A rule with an any block reads more fields than it needs. Judge on
            # the ones the clause actually supplied, not on the whole list, or a
            # rule that offers four ways to satisfy it can never qualify.
            as_used = [k for k in as_fields if k in (state.get("params") or {})]
            if as_used and all(k in a_evidence for k in as_used):
                d["from_text"] = True
                d["evidence"] = [a_evidence[k] for k in as_used]
            out.append(d)
        return {"findings": out,
                "trace": [f"assess: {len(out)} rules fired"]}

    def _verify(self, state):
        """
        Every grounded finding must carry a span that genuinely supports it.

        Three checks, and all three must pass:

        1. the span opens at the section the rule named
        2. the span is long enough to be a provision rather than a heading
        3. the span contains the rule's anchor phrase

        Checks 2 and 3 exist because check 1 alone verifies where a passage starts
        and nothing about what it says. Under check 1 only, the one-line heading
        "74. Compensation for breach of contract where penalty stipulated for"
        passed as the law a finding rested on, as did a page of a research group's
        navigation text. Both opened in the right place and neither supported
        anything. A citation you cannot read is not a citation.
        """
        as_kept, as_rejected, as_notes = [], [], []

        for f in state.get("findings") or []:
            if f["basis"] == "norm":
                f["verified"] = True
                as_kept.append(f)
                continue

            span = f.get("citation_span") or ""
            if not span:
                f["reject_reason"] = "no citation span retrieved"
                as_rejected.append(f)
                as_notes.append(f"{f['rule_id']} had no span")
                continue

            section = (f.get("source") or {}).get("section")
            if section and not re.search(rf"\b{re.escape(str(section))}\b", span[:120]):
                f["reject_reason"] = f"span does not open at section {section}"
                as_rejected.append(f)
                as_notes.append(f"{f['rule_id']} span mismatch")
                continue

            # A heading is not a provision. The shortest real section in the corpus
            # runs well past this; an arrangement-of-sections line does not.
            a_span = " ".join(span.split())
            if len(a_span) < MIN_SPAN_CHARS:
                f["reject_reason"] = (f"span is {len(a_span)} chars, too short to be "
                                      f"the provision rather than its heading")
                as_rejected.append(f)
                as_notes.append(f"{f['rule_id']} heading-only span")
                continue

            # The anchor is the phrase the rule's claim actually rests on. If the
            # retrieved text does not contain it, whatever came back is not the
            # passage that supports this finding, however correctly it is numbered.
            as_anchors = f.get("anchors") or (f.get("rule") or {}).get("anchors") or []
            if as_anchors and not _anchor_present(a_span, as_anchors):
                f["reject_reason"] = (f"span does not contain any anchor phrase "
                                      f"{as_anchors}")
                as_rejected.append(f)
                as_notes.append(f"{f['rule_id']} anchor missing")
                continue

            f["verified"] = True
            as_kept.append(f)

        return {"findings": as_kept, "rejected": as_rejected, "notes": as_notes,
                "retries": state.get("retries", 0) + (1 if as_rejected else 0),
                "trace": [f"verify: {len(as_kept)} passed, {len(as_rejected)} rejected"]}

    def _aggregate(self, state):
        """
        Keep only findings the reader should act on.

        Some rules exist to say a clause is fine, such as an in-term restraint being
        enforceable. Those carry risky=False and were rendering in the findings list
        as "[none]", which in a report of risks reads like a risk. They are recorded
        as reassurances instead, so the count on screen is a count of problems.
        """
        as_all = state.get("findings") or []
        as_risky = [f for f in as_all if f.get("risky")]
        as_ok = [f for f in as_all if not f.get("risky")]

        as_risky.sort(key=lambda f: -{"high": 3, "medium": 2, "low": 1}
                      .get(f["severity"], 0))
        return {"findings": as_risky, "reassurances": as_ok,
                "trace": [f"aggregate: {len(as_risky)} findings, "
                          f"{len(as_ok)} reassurances"]}

    # ---------- edges ----------

    def _after_triage(self, state):
        return "plan" if state.get("triage_keep") else "aggregate"

    def _after_verify(self, state):
        """Retry only if something was rejected AND we have budget left."""
        if state.get("rejected") and state.get("retries", 0) < MAX_RETRIES:
            return "plan"
        return "aggregate"

    def _build(self):
        g = StateGraph(AuditState)
        g.add_node("triage", self._triage)
        g.add_node("plan", self._plan)
        g.add_node("act", self._act)
        g.add_node("assess", self._assess)
        g.add_node("verify", self._verify)
        g.add_node("aggregate", self._aggregate)

        g.set_entry_point("triage")
        g.add_conditional_edges("triage", self._after_triage,
                                {"plan": "plan", "aggregate": "aggregate"})
        g.add_edge("plan", "act")
        g.add_edge("act", "assess")
        g.add_edge("assess", "verify")
        g.add_conditional_edges("verify", self._after_verify,
                                {"plan": "plan", "aggregate": "aggregate"})
        g.add_edge("aggregate", END)
        return g.compile()

    # ---------- entry ----------

    def run(self, doc_type, clause_text, as_params=None, clause_id="ad-hoc",
            always_extract=False, doc_params=None):
        state = {"doc_type": doc_type, "clause_text": clause_text,
                 "clause_id": clause_id, "retries": 0, "notes": [], "trace": [],
                 "always_extract": always_extract,
                 "doc_params": doc_params or {}}
        if as_params is not None:
            state["params"] = as_params
        return self.graph.invoke(state)


def render(as_results, show_trace=False):
    as_flagged = [r for r in as_results if r.get("findings")]
    print(f"\n{'=' * 72}")
    print(f"{len(as_results)} clauses, {len(as_flagged)} flagged")
    print("=" * 72)

    for r in as_results:
        if not r.get("findings") and not show_trace:
            continue
        print(f"\n{(r.get('heading') or r['clause_text'][:70]).strip()}")
        for f in r.get("findings", []):
            mark = {"high": "[HIGH]", "medium": "[MED ]", "low": "[LOW ]"}.get(f["severity"], "[    ]")
            print(f"  {mark} {f['message']}")
            print(f"         {f['basis']} ({f['rule_id']})  verified={f['verified']}")
            if f.get("citation_span"):
                print(f"         cited: {f['citation_span'][:170]}...")
        for f in r.get("reassurances", []):
            print(f"  [ OK  ] {f['message']}")
        for f in r.get("rejected", []):
            print(f"  [DROP] {f['rule_id']}: {f['reject_reason']}")
        if show_trace:
            for t in r.get("trace", []):
                print(f"         . {t}")

    print(f"\n{'=' * 72}\nFlags for review, not legal advice.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?")
    ap.add_argument("doc_type")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--trace", action="store_true", help="show the graph path per clause")
    a_args = ap.parse_args()

    as_extractor = None
    if not a_args.demo:
        from extract import Extractor
        as_extractor = Extractor()

    as_agent = ClauseAgent(extractor=as_extractor)
    as_results = []

    if a_args.demo:
        from audit import DEMO
        for text, as_params in DEMO[a_args.doc_type]:
            r = as_agent.run(a_args.doc_type, text, as_params=as_params)
            r["heading"] = text[:70]
            as_results.append(r)
    else:
        if not a_args.path:
            print("give a file path, or use --demo")
            sys.exit(1)
        as_clauses = segment.run(a_args.path, a_args.doc_type, quiet=True)
        if a_args.limit:
            as_clauses = as_clauses[:a_args.limit]
        for i, c in enumerate(as_clauses, 1):
            r = as_agent.run(a_args.doc_type, c.text, clause_id=c.clause_id)
            r["heading"] = c.heading
            as_results.append(r)
            print(f"  [{i}/{len(as_clauses)}] {len(r.get('findings', []))} findings")

    render(as_results, show_trace=a_args.trace)
