"""
Facts about the whole document, read by pattern, before any clause is examined.

Why this exists
---------------
Some rules make a claim about the agreement, not about the clause in front of
them. R-LOAN-04 says "penal charges are referred to but never quantified". Never
is a word about the whole document, and the rule was deciding it from a single
clause in isolation.

It fired on the auto-debit clause of a loan agreement, which mentions reimbursing
bank charges and names no figure. Four clauses earlier the same agreement set the
late charge at 2% per month. The finding was wrong, and it carried a correctly
retrieved RBI passage while being wrong, which is worse than being wrong quietly:
the citation made it look checked.

R-RENT-10 had the mirror image of the same flaw. It asks for `registered: false`,
but a document with no registration clause never produces that field at all, so
the value is None, so the rule silently cannot fire on exactly the documents it
exists to catch. Absence of a clause is a property of the document. No clause can
report it.

What goes in here
-----------------
Only facts that are genuinely document-scoped and genuinely readable by pattern.
Everything else stays with the model and the clause it belongs to. Every key is
prefixed `doc__` so a rule opts into document scope visibly, and so a document
fact can never collide with a clause fact and silently overwrite it.

Every key for the type is always emitted, including the false ones. A rule written
as `doc__registration_mentioned: false` only matches when the key is present and
false; if the scanner stayed silent the comparison would see None and fail closed,
which is the bug this module exists to remove.
"""

import re

# Money as it is actually written in Indian contracts, plus bare percentages.
# Unlike deterministic.py, a percentage counts here: "2% per month" is exactly how
# a late charge gets quantified, and refusing to see it was half the problem.
# Number words matter here. Indian drafting writes "twenty-four percent per annum"
# at least as often as "24%", and a scanner that only sees digits reports a rate
# that is plainly stated as undisclosed.
AS_NUMWORD = (r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
              r"fifteen|eighteen|twenty|twenty[- ]four|twenty[- ]five|thirty|"
              r"thirty[- ]six|forty|fifty)")

AS_AMOUNT = (r"(?:(?:rs\.?|inr|₹)\s*[\d,]+(?:\.\d+)?"
             r"|\b\d+(?:\.\d+)?\s*%"
             r"|\b\d+(?:\.\d+)?\s*(?:per\s*cent|percent)\b"
             r"|\b" + AS_NUMWORD + r"\s*(?:per\s*cent|percent)\b"
             r"|\b[\d.,]+\s*(?:lakh|lakhs|crore|crores)\b)")

# Wording that says the amount is deliberately not being given. This is a stronger
# signal than silence: a clause that reserves the quantum to the lender's decision
# has quantified nothing, however many figures appear elsewhere in the agreement.
AS_QUANTUM_WITHHELD = (
    r"(?:not\s+(?:be\s+)?required\s+to\s+disclose|without\s+disclos\w+"
    r"|as\s+(?:the\s+)?(?:lender|bank|company)\s+may\s+(?:decide|determine|deem)"
    r"|at\s+(?:the\s+)?(?:lender|bank|company)(?:'s|s')?\s+(?:sole\s+)?discretion"
    r"|quantum\s+(?:of\s+such\s+)?charges?\s+(?:shall|may|will)\s+(?:be\s+)?"
    r"(?:decided|determined|notified))")

# Language that introduces a charge for paying late or defaulting. Kept tight:
# "charges" alone appears in every agreement ever written and would make the
# quantified test meaningless.
AS_PENAL_LEAD = (r"(?:penal\s+(?:charge|interest|sum)\w*|penalty"
                 r"|late\s+(?:charge|fee|payment\s+charge)\w*"
                 r"|default\s+(?:charge|interest)\w*"
                 r"|overdue\s+(?:charge|interest)\w*"
                 r"|delayed\s+payment\s+charge\w*)")

AS_PREPAY_LEAD = (r"(?:pre-?payment|pre-?closure|foreclosure|part-?payment"
                  r"|early\s+repayment)")


def _clip(as_text, a_start, a_end, a_window):
    """
    A window around a match that does not run into the neighbouring clause.

    Without this the heading "3. DEFAULT INTEREST" reached backwards past a clause
    boundary, found the principal amount of the loan sitting in clause 2, and
    reported the default interest as quantified. It was not. A window that crosses
    a clause boundary is not evidence about the clause it started in, and getting
    this wrong suppresses a true finding, which is the expensive direction.
    """
    a, b = max(0, a_start - a_window), min(len(as_text), a_end + a_window)
    a_left, a_right = as_text[a:a_start], as_text[a_end:b]

    # A numbered heading or a blank line is where one provision stops and the next
    # begins. Keep only the part of the window on this side of the nearest one.
    a_bound = re.compile(r"\n\s*(?:\d{1,2}[.)]\s|\n)")

    as_left_cuts = [m.end() for m in a_bound.finditer(a_left)]
    if as_left_cuts:
        a_left = a_left[as_left_cuts[-1]:]

    m = a_bound.search(a_right)
    if m:
        a_right = a_right[:m.start()]

    return a_left + as_text[a_start:a_end] + a_right


def _quantified(as_text, a_lead, a_window=200):
    """
    Does a charge of this kind appear anywhere with a number attached.

    The number has to sit in the same provision as the charge it belongs to, which
    is what _clip enforces. A rupee figure three clauses away is not the quantum of
    this charge, and treating it as one silently switches off a finding.
    """
    for m in re.finditer(a_lead, as_text, re.I):
        a_win = _clip(as_text, m.start(), m.end(), a_window)
        if re.search(AS_AMOUNT, a_win, re.I):
            return True, " ".join(a_win.split())
    return False, None


def _withheld(as_text, a_lead, a_window=200):
    """Does the document say, in terms, that the amount is not being disclosed."""
    for m in re.finditer(a_lead, as_text, re.I):
        a_win = _clip(as_text, m.start(), m.end(), a_window)
        if re.search(AS_QUANTUM_WITHHELD, a_win, re.I):
            return True, " ".join(a_win.split())
    return False, None


def _mentioned(as_text, a_pattern):
    m = re.search(a_pattern, as_text, re.I)
    if not m:
        return False, None
    return True, " ".join(as_text[max(0, m.start() - 60):m.end() + 100].split())


AS_WORD_MONTHS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "eighteen": 18,
    "twenty-four": 24, "twenty four": 24, "thirty-six": 36, "thirty six": 36,
}


def _term_months(as_text):
    """
    The stated term of the agreement, in months, where the document says it plainly.

    Only the explicit forms. A term inferred from two dates is a calculation, and a
    calculation that is wrong here flips a compulsory-registration finding on or
    off, so it is left to the model and the clause it appears in.
    """
    as_hits = []

    for m in re.finditer(
            r"(?:period|term|tenure|tenancy|duration)\s+of\s+"
            # The bracket takes a number or a word. Indian leases state the term as
            # "eleven (11) months" and "11 (eleven) months" about equally, and only
            # the first form was being read.
            r"(\d{1,3}|[a-z]+(?:[- ][a-z]+)?)\s*(?:\(\s*[\w-]+\s*\))?\s*(month|year)s?",
            as_text, re.I):
        a_raw, a_unit = m.group(1).strip().lower(), m.group(2).lower()
        a_n = int(a_raw) if a_raw.isdigit() else AS_WORD_MONTHS.get(a_raw)
        if a_n is None:
            continue
        as_hits.append((a_n * 12 if a_unit.startswith("year") else a_n,
                        " ".join(m.group(0).split())))

    for m in re.finditer(r"\b(\d{1,3})\s*[- ]\s*month\s+(?:term|period|tenancy|tenure|lease)\b",
                         as_text, re.I):
        as_hits.append((int(m.group(1)), " ".join(m.group(0).split())))

    # The key-terms table. Indian rent agreements very often open with a table whose
    # row reads "Term  01 October 2026 to 30 September 2027 (12 months)": a label,
    # the dates, and the length in brackets, with no "of" anywhere. The forms above
    # all need "term of 12 months", so on a real 12-month lease this returned nothing
    # and R-RENT-10 could not see that a compulsorily registrable lease was never
    # registered. The window stops at a full stop or a line break, so a figure from
    # the next row cannot be read as this one.
    for m in re.finditer(
            r"\b(?:term|tenure|lease\s+period|period\s+of\s+(?:lease|tenancy|licen[cs]e))\b"
            r"[^.\n]{0,90}?\(?\s*(\d{1,3})\s*(month|year)s?\s*\)?",
            as_text, re.I):
        a_n = int(m.group(1))
        a_months = a_n * 12 if m.group(2).lower().startswith("year") else a_n
        # A lease is not a thousand months long. Anything past fifty years is a
        # figure that belongs to something else on the line, not a term.
        if 0 < a_months <= 600:
            as_hits.append((a_months, " ".join(m.group(0).split())))

    if not as_hits:
        return None, None
    # The longest stated period wins. A lease often names a short lock-in and a
    # longer term in the same document, and registration turns on the term.
    a_n, a_ev = max(as_hits, key=lambda h: h[0])
    return a_n, a_ev


def _deposit_months(as_text):
    """
    The security deposit in months of rent, where the document states it plainly.

    It usually sits in the key-terms table at the top of a lease, the one long block
    a model reads least reliably. A fictional 12-month lease stating "Security Deposit
    INR 94,500 (equivalent to three months' rent)" was flagged on one run and missed on
    the next, and the rule it feeds is the one most leases in India break.

    Two forms, strictly. Months stated in words are read as written. A deposit stated
    only in money is divided by a monthly rent stated in money, and only when each
    appears as exactly one figure, so an ambiguous document gives no answer rather
    than a wrong one.
    """
    # Number words only. A free "word, optionally two words" took "to three" as the
    # number in "equivalent to three months' rent", matched nothing, and the stated
    # months were silently skipped for the arithmetic below.
    a_words = "|".join(sorted((re.escape(w) for w in AS_WORD_MONTHS), key=len, reverse=True))
    a_num = rf"(\d{{1,2}}|{a_words})\b\s*(?:\(\s*(\d{{1,2}}|{a_words})\s*\))?"
    a_months = r"\s*months?['\u2019]?\s*(?:of\s+(?:the\s+)?)?(?:monthly\s+)?rent"

    def number(a_word, a_bracket):
        for a_try in (a_word, a_bracket):
            a_try = (a_try or "").strip().lower()
            if a_try.isdigit():
                return int(a_try)
            if a_try in AS_WORD_MONTHS:
                return AS_WORD_MONTHS[a_try]
        return None

    as_hits = {}
    for a_pat in (r"deposit[^.\n]{0,100}?\b" + a_num + a_months,
                  r"\b" + a_num + a_months + r"[^.\n]{0,40}?deposit"):
        for m in re.finditer(a_pat, as_text, re.I):
            a_n = number(m.group(1), m.group(2))
            if a_n and 0 < a_n <= 24:
                as_hits.setdefault(a_n, " ".join(m.group(0).split()))
    if len(as_hits) == 1:
        return next(iter(as_hits.items()))
    if len(as_hits) > 1:
        return None, None

    def money(a_raw):
        return float(a_raw.replace(",", ""))

    as_deposit = {money(m.group(1)): " ".join(m.group(0).split()) for m in re.finditer(
        r"security\s+deposit[^.\n\d]{0,40}?(?:rs\.?|inr|\u20b9)\s*([\d,]+(?:\.\d+)?)",
        as_text, re.I)}
    # A monthly rent, not the maintenance charge or the late fee on the next line:
    # labelled "monthly rent", or called rent and followed by "per month".
    as_rent = set()
    for m in re.finditer(r"monthly\s+rent[^.\n\d]{0,30}?(?:rs\.?|inr|\u20b9)\s*([\d,]+)",
                         as_text, re.I):
        as_rent.add(money(m.group(1)))
    for m in re.finditer(r"\brent\b[^.\n\d]{0,30}?(?:rs\.?|inr|\u20b9)\s*([\d,]+)"
                         r"(?:/-)?\s*(?:per\s+month|a\s+month|monthly|p\.?\s?m\.?)",
                         as_text, re.I):
        as_rent.add(money(m.group(1)))
    if len(as_deposit) == 1 and len(as_rent) == 1:
        (a_dep, a_ev), a_rent = next(iter(as_deposit.items())), next(iter(as_rent))
        if a_rent > 0:
            return round(a_dep / a_rent, 1), a_ev
    return None, None


def read_document(doc_type, as_text):
    """
    Return (params, evidence) for the whole document.

    Never raises and never returns a partial key set for the type. A caller merges
    these under the clause parameters, where the `doc__` prefix guarantees they
    cannot shadow anything the model read.
    """
    as_text = as_text or ""
    as_params, as_evidence = {}, {}

    def put(a_key, a_value, a_ev):
        as_params[a_key] = a_value
        if a_ev:
            as_evidence[a_key] = a_ev[:160]

    if doc_type == "loan":
        a_q, a_ev = _quantified(as_text, AS_PENAL_LEAD)
        put("doc__penal_charge_quantified", a_q, a_ev)

        # Stated refusal to give a figure. Kept separate from the quantified test
        # because the two are not opposites: an agreement can name a late charge in
        # one clause and reserve the penal charge to its own discretion in the next,
        # and only the second is the disclosure failure the RBI circular addresses.
        a_w, a_ev = _withheld(as_text, AS_PENAL_LEAD)
        put("doc__penal_quantum_withheld", a_w, a_ev)

        a_p, a_ev = _quantified(as_text, AS_PREPAY_LEAD)
        put("doc__prepayment_charge_quantified", a_p, a_ev)

        a_k, a_ev = _mentioned(
            as_text,
            r"(key\s+fact\s+statement|\bkfs\b|annual\s+percentage\s+rate|\bapr\b)")
        put("doc__kfs_present", a_k, a_ev)

    elif doc_type == "rental":
        a_r, a_ev = _mentioned(
            as_text,
            r"(registrat(?:ion|ed)|sub-?registrar|stamp\s+dut(?:y|ies)|registration\s+act)")
        put("doc__registration_mentioned", a_r, a_ev)

        a_n, a_ev = _term_months(as_text)
        if a_n is not None:
            put("doc__term_months", a_n, a_ev)

        a_d, a_ev = _deposit_months(as_text)
        if a_d is not None:
            put("doc__deposit_months", a_d, a_ev)

    elif doc_type == "employment":
        a_n, a_ev = _mentioned(as_text, r"(notice\s+period|notice\s+of\s+\w+\s+(?:month|day))")
        put("doc__notice_period_mentioned", a_n, a_ev)

    return as_params, as_evidence


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("usage: python src/docscan.py <file> <doc_type>")
        sys.exit(1)
    a_txt = open(sys.argv[1], encoding="utf-8", errors="ignore").read()
    as_p, as_e = read_document(sys.argv[2], a_txt)
    for k in sorted(as_p):
        print(f"{k:<38} {as_p[k]}")
        if k in as_e:
            print(f"{'':<38} evidence: {as_e[k][:110]}")
