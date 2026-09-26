"""
Negotiation drafting, behind a human approval gate.

    from negotiate import draft_message
    text = draft_message("employment", findings, tone="firm")

The tool never sends anything. It produces text the user reads, edits and sends
themselves, which is the correct design for a system that flags legal risk without
being a lawyer: the human stays the one making the ask.

The draft is built from the findings the verify node already accepted, so every
point in the message traces back to a rule that carried a citation. Nothing new is
invented at drafting time.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import llm   # noqa: E402

# The model is never told to name an Act or a section. Every legal sentence is built
# here, from the finding's own structured data, and the model is only allowed to
# smooth the prose around it.
#
# This exists because the drafter used to be handed "section 74 of the cited Act"
# with the Act's name withheld, and filled the gap itself: one draft cited
# "Section 74 of the Employment Standards Act", which is not a law that exists in
# India. A tool whose whole claim is that it never generates law cannot generate law
# in the one artefact the user actually sends to their employer.
ACT_TITLES = {
    "indian_contract_act_1872": "the Indian Contract Act, 1872",
    "copyright_act_1957": "the Copyright Act, 1957",
    "specific_relief_act_1963": "the Specific Relief Act, 1963",
    "transfer_of_property_act_1882": "the Transfer of Property Act, 1882",
    "registration_act_1908": "the Registration Act, 1908",
    "model_tenancy_act_2021": "the Model Tenancy Act, 2021",
    "rbi_penal_charges_2023": "the RBI circular on penal charges dated 18 August 2023",
}


# Curly quotes are quotes. The model writes “ ” at least as often as ", and the
# check below split on the straight mark only, so a draft that put Clarity's own
# finding in quotation marks and said the lease stated it went through unchecked.
AS_QUOTE_MAP = str.maketrans({"\u201c": '"', "\u201d": '"', "\u201e": '"',
                              "\u201f": '"', "\u00ab": '"', "\u00bb": '"',
                              "\u2018": "'", "\u2019": "'"})


def _straight_quotes(a_text):
    return (a_text or "").translate(AS_QUOTE_MAP)


_AS_DOC_SCOPE = {}


def _document_scope_rules(doc_type):
    """Rules that are true of the whole agreement, which have no clause to quote."""
    if doc_type not in _AS_DOC_SCOPE:
        try:
            from rules import RuleEngine
            _AS_DOC_SCOPE[doc_type] = {r["id"] for r in RuleEngine().rules_for(doc_type)
                                       if r.get("scope") == "document"}
        except Exception:
            _AS_DOC_SCOPE[doc_type] = set()
    return _AS_DOC_SCOPE[doc_type]


def clause_quote(f, limit=320):
    """
    The clause body, with the heading taken off the front.

    Segmentation keeps a heading attached to the block it introduces, which is
    right for reading and wrong for quoting. One draft opened with 'The clause
    states: "Terms of appointment Your probation period..."'. "Terms of
    appointment" is a heading in the letter, not words of the contract, and quoting
    it back as contract language is the kind of thing a reader notices immediately.
    """
    a_text = " ".join((f.get("clause_text") or "").split())
    a_head = " ".join((f.get("heading") or "").split())
    if a_head and a_text.lower().startswith(a_head.lower()):
        a_text = a_text[len(a_head):].lstrip(" :.-\u2014")
    return a_text[:limit] + ("..." if len(a_text) > limit else "")


def legal_sentence(f):
    """The one sentence in a draft point that states law. Deterministic, always."""
    src = f.get("source") or {}
    act = ACT_TITLES.get(src.get("act"))
    sec = src.get("section")
    basis = f.get("basis")

    if basis == "statutory" and act and sec:
        return f"Section {sec} of {act} speaks directly to this."
    if basis == "regulatory" and act:
        return f"This is the subject of {act}, which binds regulated lenders."
    if basis == "policy" and act and sec:
        return (f"Section {sec} of {act} sets the benchmark here. It is a model law, "
                f"binding only in states that have adopted it.")
    if basis == "policy" and act:
        return (f"{act.capitalize()} sets the benchmark here, though as a model law it "
                f"binds only in states that have adopted it.")
    # No law applies, so the draft states none. It used to say "No statute settles
    # this point", which is true, and which in a message sent to an employer or a
    # landlord reads as conceding the point before asking for it. The page still
    # labels these findings "No law on point"; the draft simply asks on the merits.
    return None


def validate_draft(text, as_findings, doc_type):
    """
    Reject a draft that states law the findings do not support.

    Returns a list of problems. An empty list means the draft is safe to show. The
    caller falls back to the deterministic template when it is not, because a draft
    the user sends to their employer is the last place to be relaxed about this.
    """
    import re as _re
    as_problems = []

    allowed_sections = {str((f.get("source") or {}).get("section"))
                        for f in as_findings if (f.get("source") or {}).get("section")}
    allowed_acts = {ACT_TITLES.get((f.get("source") or {}).get("act"), "").lower()
                    for f in as_findings}
    allowed_acts.discard("")

    for m in _re.finditer(r"[Ss]ection\s+(\d+[A-Za-z]?)", text):
        if m.group(1) not in allowed_sections:
            as_problems.append(f"cites section {m.group(1)}, which no finding supports")

    for m in _re.finditer(r"\b([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*\s+Act(?:,?\s+\d{4})?)", text):
        named = m.group(1).lower()
        if not any(named in a or a in named for a in allowed_acts):
            as_problems.append(f"names \"{m.group(1)}\", which is not an Act any finding cites")

    # Pair the quotation marks by position rather than by regex. A pattern like
    # "([^"]{25,})" happily matches from the closing quote of one short quotation to
    # the opening quote of the next, capturing the prose in between and reporting a
    # fabricated quote that nobody wrote.
    as_parts = _straight_quotes(text).split('"')
    as_quoted = [as_parts[i] for i in range(1, len(as_parts), 2)]
    as_haystack = [" ".join(_straight_quotes(f.get("clause_text")).split()).lower()
                   for f in as_findings]
    as_haystack += [" ".join(_straight_quotes(f.get("heading")).split()).lower()
                    for f in as_findings]

    for q in as_quoted:
        a_q = " ".join(q.split()).rstrip(". ").lower()
        if len(a_q) < 25:
            continue
        if not any(a_q[:60] in h for h in as_haystack if h):
            as_problems.append(f"quotes text that is not in the contract: \"{q[:50]}\"")

    if "**" in text or "##" in text:
        as_problems.append("contains raw markdown")

    # A comparison against other contracts is a norm-tier claim. It is a claim about
    # a corpus, and it is only honest when a norm-tier finding is behind it.
    #
    # This fired on a real draft. A statutory finding about a service bond came out
    # as "not standard for comparable agreements", which is both the wrong register
    # for a section 74 point and, on the facts, doubtful: service bonds are common in
    # Indian analytics and consulting. The cause was upstream, the finding's source
    # being dropped before the drafter saw it, but the check belongs here too, since
    # the model can reach for this phrasing on its own.
    a_has_norm = any(f.get("basis") == "norm" for f in as_findings)
    if not a_has_norm:
        for phrase in ("comparable agreement", "similar agreement", "similar contract",
                       "market standard", "not standard", "industry standard",
                       "unusual compared", "atypical", "uncommon in"):
            if phrase in text.lower():
                as_problems.append(
                    f"claims \"{phrase}\", a comparison against other contracts, "
                    f"when no norm-tier finding supports one")
                break

    return as_problems


TONES = {
    "soft": "polite and collaborative, assuming good faith and a simple oversight",
    "firm": "direct and businesslike, clear that these points need to change",
    "formal": "formal and precise, suitable for a lawyer to read",
}

COUNTERPARTY = {"employment": "the employer", "rental": "the landlord",
                "loan": "the lender"}


def build_prompt(doc_type, as_findings, tone):
    as_points = []
    as_doc = _document_scope_rules(doc_type)
    for i, f in enumerate(as_findings, 1):
        line = f"{i}. [{f['severity']}] {f['message']}"
        # The clause itself, so the draft can quote this contract rather than
        # paraphrase a rule. Without it every document produced the same letter.
        if f.get("rule_id") in as_doc:
            # True of the agreement as a whole, so there is no clause to quote. Given
            # the opening clause here, the model quoted this finding back as if the
            # lease itself said it.
            line += ("\n   This is about the agreement as a whole, not one clause. Do "
                     "not quote the contract for this point; say what it does not provide.")
        else:
            a_clause = clause_quote(f)
            if a_clause:
                line += f"\n   The clause says: \"{a_clause}\""
        # The legal sentence is built here, not by the model. It is handed over
        # finished, and the model is told to reproduce it verbatim.
        a_legal = legal_sentence(f)
        if a_legal:
            line += f"\n   Legal sentence, reproduce exactly: {a_legal}"
        else:
            line += "\n   No law applies to this point. Ask for the change on its merits and cite no law."
        as_points.append(line)

    return f"""Draft a short message asking {COUNTERPARTY.get(doc_type, 'the other side')} to change specific clauses.

Tone: {TONES.get(tone, TONES['firm'])}

Points to raise, worst first:
{chr(10).join(as_points)}

Rules for the draft:
- Open with one sentence of context, then go straight to the asks
- One short paragraph per point, and say what change is wanted, not just what is wrong
- Where a point has a "Legal sentence", reproduce it word for word.
  Never write a section number or the name of an Act that is not in that sentence,
  and never add one of your own. If a point has no legal sentence, do not imply any
  law applies to it
- Do not claim to be a lawyer and do not threaten litigation
- No more than 250 words
- Plain English, no legalese, no em dashes
- No markdown. No asterisks, no hash marks, no bold
- Quote the contract only where the clause text is given above, and quote it exactly

- Refer to what this contract actually says. Where a clause is quoted above, name the
  specific term, figure or period it uses rather than restating the generic rule
- Every document is different, so the message must read as if written about this one

Return only the message body. No subject line, no preamble, no sign-off placeholder."""


def draft_message(doc_type, as_findings, tone="firm"):
    as_kept = [f for f in as_findings if f.get("verified") and f.get("risky")]
    if not as_kept:
        return None

    ok, why = llm.available()
    if not ok:
        raise RuntimeError(why)

    a_text = llm.complete(build_prompt(doc_type, as_kept, tone), max_tokens=800)

    # A draft that states law the findings do not support is worse than a plain one,
    # because the user sends it under their own name. One retry, then the
    # deterministic template, which cannot invent a section because it never writes
    # one that is not already in the finding.
    as_problems = validate_draft(a_text, as_kept, doc_type)
    if as_problems:
        a_text = llm.complete(
            build_prompt(doc_type, as_kept, tone)
            + "\n\nThe previous attempt was rejected for: "
            + "; ".join(as_problems)
            + ". Use only the legal sentences given above, word for word.",
            max_tokens=800)
        as_problems = validate_draft(a_text, as_kept, doc_type)
        if as_problems:
            return draft_offline(doc_type, as_findings)
    return a_text


def draft_offline(doc_type, as_findings):
    """Deterministic fallback so the feature works with no API key."""
    as_doc = _document_scope_rules(doc_type)
    as_kept = [f for f in as_findings if f.get("verified") and f.get("risky")]
    if not as_kept:
        return None

    a_doc = {"employment": "the offer letter", "rental": "the rent agreement",
             "loan": "the loan agreement"}.get(doc_type, "the draft")
    n = len(as_kept)
    a_word = "point" if n == 1 else "points"

    as_lines = [f"I have read through {a_doc} and there {'is' if n == 1 else 'are'} "
                f"{n} {a_word} I would like to discuss before signing.", ""]

    for i, f in enumerate(as_kept, 1):
        # Name the clause and quote it. Without this every document produced a
        # letter identical to every other document, which is useless to send: the
        # reader cannot tell which clause is being objected to.
        a_head = (f.get("heading") or "").strip()
        a_where = f'Clause "{a_head}"' if a_head else "One clause"

        ask = f["message"].rstrip(".")
        a_legal = legal_sentence(f)
        as_lines.append(f"{i}. {a_where}. {ask}." + (f" {a_legal}" if a_legal else ""))

        a_quote = "" if f.get("rule_id") in as_doc else clause_quote(f, limit=260)
        if a_quote:
            as_lines.append(f'   As drafted: "{a_quote}"')
        as_lines.append("")

    as_lines += [f"Could we go through {'this' if n == 1 else 'these'} before I sign? "
                 f"Happy to discuss."]
    return "\n".join(as_lines)
