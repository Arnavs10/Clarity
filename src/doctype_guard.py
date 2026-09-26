"""
Does this document look like the type the reader picked?

Why this exists
---------------
The dropdown was trusted absolutely. `_run` checked that the slug was one of the
three configured types and never checked the document against it. An internship
offer letter uploaded as a loan agreement was read by the loan rules, produced a
high-severity finding saying the entire balance could be called in with no notice,
and attached it to the clause where the candidate confirms his educational
qualifications. The negotiation draft then quoted that clause back under that
heading. There is no balance. There is no lender.

Nothing anywhere in the pipeline noticed, because nothing anywhere was looking.

How it decides
--------------
A lexicon per type, matched whole-word against the whole document, scored by how
many distinct terms are present rather than how often they occur. Repetition is
not evidence: a loan agreement saying "Borrower" ninety times is no more a loan
agreement than one saying it nine times, and counting occurrences lets one stray
word in a long document outweigh a dozen real ones in a short one.

Deterministic on purpose. The model is not asked, so this cannot vary between
runs, and it cannot be talked out of its answer by a clause that happens to read
like something else.

It warns rather than refuses. A guard that blocks is a guard that is wrong about
somebody's unusual but genuine contract, and the cost of that is the whole
document. The cost of a warning is one sentence the reader can overrule.
"""

import re

# Two weights only.
#
#   strong  a word that names a party or an instrument specific to this type.
#           "Borrower" is not a word that turns up in a rent agreement by accident.
#   core    vocabulary the type uses constantly but shares with its neighbours.
#           "notice" and "termination" appear in all three, so they are worth
#           little on their own and a great deal in aggregate.
#
# Deliberately built from what the document type IS, not from the four documents
# that have been tested. Nothing here is tuned to a particular contract, and a
# term was only included if a person could say why every document of that type
# would be expected to contain it.
AS_LEXICONS = {
    "employment": {
        "strong": ["employee", "employer", "employment", "candidate", "intern",
                   "internship", "stipend", "salary", "appointment letter",
                   "probation", "designation", "remuneration", "joining date",
                   "offer letter", "ctc", "payroll", "resignation"],
        "core": ["notice period", "leave", "working hours", "confidentiality",
                 "non-compete", "termination", "duties", "reporting",
                 "performance", "company", "appraisal", "benefits"],
    },
    "rental": {
        "strong": ["tenant", "landlord", "lessor", "lessee", "tenancy",
                   "rent agreement", "lease deed", "leave and licence",
                   "leave and license", "licensee", "licensor", "premises",
                   "security deposit", "sublet", "sub-let"],
        "core": ["rent", "lease", "possession", "vacate", "occupy", "monthly rent",
                 "maintenance", "society", "furnished", "lock-in", "lock in",
                 "landlord's", "inspection", "utilities"],
    },
    "loan": {
        "strong": ["borrower", "lender", "loan agreement", "principal amount",
                   "disbursement", "disbursal", "emi", "instalment", "installment",
                   "prepayment", "foreclosure", "penal charge", "penal interest",
                   "amortisation", "amortization", "key fact statement",
                   "processing fee", "moratorium"],
        "core": ["loan", "interest rate", "repayment", "default", "principal",
                 "tenure", "overdue", "credit", "sanction", "security", "lien",
                 "set-off", "set off"],
    },
}

STRONG_WEIGHT = 3
CORE_WEIGHT = 1

# Below this share of a type's vocabulary, the document is not recognisably that
# type. Set low on purpose: a genuine contract can be terse, and the job here is
# to catch a document from an entirely different family, not to grade prose.
MIN_SIGNAL = 0.12

# The winning type has to beat the selected one by this much before the guard says
# anything. A loan agreement secured on a flat mentions premises and possession, so
# the two scores are legitimately close and a narrow lead proves nothing.
MISMATCH_RATIO = 1.8

# Under this many words, any score is noise. A paragraph pasted from the middle of
# a contract has no letterhead, no parties and no defined terms.
MIN_WORDS = 120


def _norm(as_text):
    """
    Lowercase, strip punctuation that breaks word boundaries, collapse whitespace.

    Hyphens are kept, because "non-compete", "lock-in" and "set-off" are terms in
    the lexicon and flattening the hyphen turns three phrases into six words that
    match far more loosely than intended.
    """
    a_t = (as_text or "").lower()
    a_t = a_t.replace("\u2019", "'").replace("\u2018", "'")
    a_t = re.sub(r"[^\w\s'-]", " ", a_t)
    return re.sub(r"\s+", " ", a_t).strip()


def _present(a_term, a_haystack):
    """Whole-word or whole-phrase containment. 'loan' must not match 'loaned'."""
    return re.search(rf"(?<!\w){re.escape(a_term)}(?!\w)", a_haystack) is not None


def score_one(a_haystack, as_lex):
    """Share of this type's weighted vocabulary present at least once."""
    a_total = len(as_lex["strong"]) * STRONG_WEIGHT + len(as_lex["core"]) * CORE_WEIGHT
    a_hit = 0
    as_matched = []

    for t in as_lex["strong"]:
        if _present(t, a_haystack):
            a_hit += STRONG_WEIGHT
            as_matched.append(t)
    for t in as_lex["core"]:
        if _present(t, a_haystack):
            a_hit += CORE_WEIGHT
            as_matched.append(t)

    return (a_hit / a_total if a_total else 0.0), as_matched


def classify(as_text):
    """
    Score the text against every configured type.

    Returns {slug: {"score": float, "matched": [...]}}, highest first when iterated
    through `ranked`.
    """
    a_hay = _norm(as_text)
    out = {}
    for a_slug, as_lex in AS_LEXICONS.items():
        a_score, as_matched = score_one(a_hay, as_lex)
        out[a_slug] = {"score": round(a_score, 4), "matched": as_matched}
    return out


def ranked(as_scores):
    return sorted(as_scores.items(), key=lambda kv: -kv[1]["score"])


def check(as_text, a_selected, as_labels=None):
    """
    The verdict for one document read as one type.

    Returns a dict, always with the same keys, so a caller never has to test for
    their presence:

        status    ok | mismatch | unrecognised | too_short | unknown_type
        selected  the slug the reader picked
        suggested the slug that scored highest, or None
        message   one sentence for the reader, or None when status is ok
        scores    every type's score, for the trace and for the tests

    Never raises. This runs on whatever arrived from an upload, which has already
    been null bytes, Devanagari and a renamed JPEG in testing, and a guard that
    throws on strange input is worse than no guard at all.
    """
    as_labels = as_labels or {}
    a_text = as_text or ""

    a_result = {"status": "ok", "selected": a_selected, "suggested": None,
                "message": None, "scores": {}, "words": 0}

    if a_selected not in AS_LEXICONS:
        # A type is configured that this module has no vocabulary for. Say so
        # rather than guessing: a silent pass here would be a guard that quietly
        # stops guarding the moment a fourth type is added.
        a_result["status"] = "unknown_type"
        return a_result

    a_hay = _norm(a_text)
    a_words = len(a_hay.split())
    a_result["words"] = a_words

    if a_words < MIN_WORDS:
        a_result["status"] = "too_short"
        return a_result

    as_scores = classify(a_text)
    a_result["scores"] = {k: v["score"] for k, v in as_scores.items()}

    a_mine = as_scores[a_selected]["score"]
    a_best_slug, a_best = ranked(as_scores)[0]

    def label(s):
        return as_labels.get(s, s)

    def a_an(s):
        # The labels come from the type config and are whatever the author wrote,
        # so "an Employment offer letter" has to be worked out rather than assumed.
        return ("an " if s[:1].lower() in "aeiou" else "a ") + s

    # Nothing looks like anything. Usually a scanned page that came back as noise,
    # a language the lexicons do not cover, or a document that is not a contract.
    if a_best["score"] < MIN_SIGNAL:
        a_result["status"] = "unrecognised"
        a_result["message"] = (
            "This does not read like any of the contract types configured here, so "
            "the checks may not apply to it. Read the findings with that in mind.")
        return a_result

    # Something else fits considerably better.
    if a_best_slug != a_selected and a_best["score"] >= a_mine * MISMATCH_RATIO:
        a_result["status"] = "mismatch"
        a_result["suggested"] = a_best_slug
        a_result["message"] = (
            f"This was read as {a_an(label(a_selected))}, but it looks much more "
            f"like {a_an(label(a_best_slug))}. The rules for the wrong type will "
            f"either miss what matters or flag things that are not there. Change "
            f"the document type and run it again.")
        return a_result

    # The selected type registers barely at all, even though something else does
    # not clearly win either. Worth saying, because the findings rest on it.
    if a_mine < MIN_SIGNAL:
        a_result["status"] = "unrecognised"
        a_result["suggested"] = a_best_slug if a_best_slug != a_selected else None
        a_result["message"] = (
            f"Very little in this document reads like {a_an(label(a_selected))}. "
            f"The findings below may not apply to it.")
        return a_result

    return a_result


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("usage: python src/doctype_guard.py <file> <doc_type>")
        sys.exit(1)
    a_txt = open(sys.argv[1], encoding="utf-8", errors="ignore").read()
    a_out = check(a_txt, sys.argv[2])
    print(f"status    {a_out['status']}")
    print(f"words     {a_out['words']}")
    for k, v in sorted(a_out["scores"].items(), key=lambda kv: -kv[1]):
        print(f"  {k:<12} {v:.3f}")
    if a_out["message"]:
        print(f"\n{a_out['message']}")
