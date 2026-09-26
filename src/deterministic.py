"""
Facts read straight off the clause text by pattern, with no model in the loop.

Why this exists
---------------
The design already says the model does not decide risk: it extracts facts and a
rule layer decides. That held, but it left the highest-stakes rules resting on
whether the model happened to notice a field on a given call. Auditing the same
offer letter twice gave nothing and then a high-severity flag, on identical
input. For a clause that costs ten lakh rupees, "the model usually notices" is
not good enough.

So for a small set of facts the model is not asked at all. A regex reads them,
and it reads the same way every time. Everything else still goes to the model,
because most clauses need reading rather than matching.

Each fact carries the text it matched. A fact with no evidence span is dropped,
which means a pattern change cannot quietly start asserting things the contract
does not say.

The patterns are deliberately narrow. A missed fact falls back to the model,
which is the behaviour before this file existed. A wrong fact fires a rule on a
fair contract, which is the failure that destroys the product. So when in doubt
these patterns stay silent.
"""

import re

# Fields this module owns outright. The model is never asked for them and a value
# it volunteers is discarded, because these are facts you read off the page, not
# judgements: a days range times an hours range, a bracket that was never filled.
# Without this, a model that hallucinated "weekly_hours: 200" would fire a real
# rule on a number that appears nowhere in the contract, and the finding would
# carry the same weight as one the pattern actually matched.
DETERMINISTIC_ONLY = {
    "weekly_hours",
    "weekly_hours_net",
    "unfilled_placeholder",
    "settlement_conditional",
    "unpaid_work_period",
    "material_terms_elsewhere",
}

# Money in Indian contracts, in the forms that actually appear: Rs. 10,00,000 /
# INR 2,50,000 / 10 lakhs / 2 lakh. Bare percentages are not money for this
# purpose, since a notice period expressed as a percentage is not a penalty.
AS_MONEY = r"(?:(?:rs\.?|inr|₹)\s*[\d,]+|\b[\d.,]+\s*(?:lakh|lakhs|crore|crores)\b)"

# A commitment to stay for a period. "Minimum period of service", "minimum
# tenure commitment", "shall serve a minimum of", "bond period".
AS_SERVICE_COMMITMENT = re.compile(
    r"(minimum\s+(?:period\s+of\s+service|tenure|service\s+period|term)"
    r"|minimum\s+tenure\s+commitment"
    r"|service\s+(?:bond|commitment)\b"
    r"|shall\s+serve\s+(?:a\s+minimum|for\s+a\s+minimum|the\s+company\s+for)"
    r"|bond\s+period)", re.I)

# Money that becomes payable because the person leaves, rather than money that
# is simply mentioned. The two have to appear close together or a stipend figure
# in the same paragraph would look like a penalty.
AS_EXIT_RECOVERY = re.compile(
    r"(?:liable\s+to\s+(?:a\s+)?recover|recovery\s+of|shall\s+(?:pay|refund|reimburse)"
    r"|liquidated\s+damages|indemnif\w+|forfeit\w*|penalty\s+of)"
    r"[^.]{0,160}?" + AS_MONEY, re.I)

AS_RECOVERY_THEN_REASON = re.compile(
    r"(?:discontinu\w+|leav\w+|resign\w+|exit\w*|terminat\w+|quit\w*|separation)"
    r"[^.]{0,200}?"
    r"(?:liable\s+to|recovery\s+of|shall\s+(?:pay|refund|reimburse)|liquidated\s+damages)", re.I)

AS_TRAINING_CLAWBACK = re.compile(
    r"(?:cost\s+of\s+training|training\s+cost|cost\s+of\s+the\s+training"
    r"|investment\s+(?:made|incurred))", re.I)

# Wording that purports to lock the person in rather than merely state a period.
AS_NON_NEGOTIABLE = re.compile(
    r"(not\s+negotiable|non-?negotiable|under\s+any\s+circumstance"
    r"|cannot\s+be\s+waived|no\s+\w+\s+can\s+waive|mandatory)", re.I)

# An unfilled template field. Real bracketed cross-references such as
# "clause [4]" are numbers, so digits-only brackets do not count, and neither do
# the amendment markers India Code prints, e.g. 1[14.
AS_PLACEHOLDER = re.compile(r"\[([A-Za-z][^\]\n]{2,40})\]")

AS_PLACEHOLDER_ALLOW = re.compile(
    r"^(sic|\.\.\.|etc|see|and|or|the\s+company|emphasis\s+added)$", re.I)


def _span(match, text, width=90):
    """
    The matched text with a little context, snapped to word boundaries.

    Without the snap a span opens mid-word, which reads as a bug even when the
    fact behind it is right: "t will be liable to a recovery of upto Rs. 10,00,000".
    """
    if not match:
        return None
    a, b = match.start(), match.end()
    while a > 0 and not text[a - 1].isspace():
        a -= 1
    while b < len(text) and not text[b].isspace():
        b += 1
    out = " ".join(text[a:b].split())
    return out[:width] + ("..." if len(out) > width else "")


def read_facts(doc_type, as_text):
    """
    Return (params, evidence). Both are empty when nothing matches, which leaves
    the clause exactly as the model alone would have left it.
    """
    as_params, as_evidence = {}, {}

    def put(key, match):
        ev = _span(match, as_text)
        if ev:                       # no evidence, no fact
            as_params[key] = True
            as_evidence[key] = ev

    a_lower = as_text.lower()

    # An unfilled placeholder is worth catching in every document type. The party
    # you are contracting with, or the court that hears a dispute, should not be
    # a blank left in a template.
    for m in AS_PLACEHOLDER.finditer(as_text):
        inner = m.group(1).strip()
        if AS_PLACEHOLDER_ALLOW.match(inner):
            continue
        if inner.isdigit():
            continue
        put("unfilled_placeholder", m)
        break

    if doc_type != "employment":
        return as_params, as_evidence

    a_commitment = AS_SERVICE_COMMITMENT.search(as_text)

    # A bond is a commitment to stay. On its own it is not yet a section 74
    # problem; R-EMP-03 wants money attached, and that is checked separately
    # below, exactly as the rule requires.
    if a_commitment:
        put("bond_present", a_commitment)

        # A period someone is told they cannot negotiate their way out of is the
        # clause that purports to compel continued service.
        a_locked = AS_NON_NEGOTIABLE.search(as_text)
        if a_locked:
            put("compels_continued_service", a_locked)

    # Money payable on leaving. Two shapes: the money follows the trigger word,
    # or the reason for leaving is stated first and the liability follows.
    a_exit = AS_EXIT_RECOVERY.search(as_text)
    if a_exit and (a_commitment or AS_RECOVERY_THEN_REASON.search(as_text)):
        put("recovery_on_early_exit", a_exit)
        put("bond_present", a_commitment or a_exit)

    if AS_TRAINING_CLAWBACK.search(as_text) and re.search(AS_MONEY, a_lower):
        put("training_cost_clawback", AS_TRAINING_CLAWBACK.search(as_text))

    a_more, a_more_ev = read_more_facts(doc_type, as_text)
    as_params.update(a_more)
    as_evidence.update(a_more_ev)

    return as_params, as_evidence


# ---------------------------------------------------------------------------
# Facts below this line came from reading real Indian offer letters and asking
# what a careful reader would object to, not from any one document. Each one is
# a pattern that recurs across employers: hours that exceed the state cap, money
# already earned held back as leverage, work that is not paid, and terms that
# live in a document you have not been shown.
# ---------------------------------------------------------------------------

AS_DAY_SPAN = re.compile(
    r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s*(?:-|to|\u2013|\u2014)\s*"
    r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)", re.I)

AS_TIME_SPAN = re.compile(
    r"(\d{1,2})[:.](\d{2})\s*(am|pm)?\s*(?:-|to|\u2013|\u2014)\s*(\d{1,2})[:.](\d{2})\s*(am|pm)?", re.I)

AS_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

# Money already earned, held back until the person does something else.
AS_SETTLEMENT_LEVERAGE = re.compile(
    r"(?:full\s+and\s+final\s+settlement|final\s+settlement|experience\s+certificate"
    r"|relieving\s+letter|service\s+certificate)", re.I)

AS_CONDITIONAL = re.compile(
    r"(?:is\s+mandatory|only\s+(?:upon|after|if)|subject\s+to|shall\s+not\s+be\s+(?:issued|released|paid)"
    r"|withheld|conditional\s+upon|required\s+(?:to|in\s+order)\s+to\s+receive)", re.I)

# Work with no pay attached.
AS_UNPAID = re.compile(
    r"(?:\bunpaid\b|without\s+(?:any\s+)?(?:pay|stipend|remuneration|salary)"
    r"|no\s+(?:stipend|salary|remuneration)\s+(?:shall\s+be|will\s+be|is)\s+(?:paid|payable))", re.I)

AS_WORK_PERIOD = re.compile(
    r"(?:training|probation|orientation|induction|internship|on-?the-?job|ojt|notice\s+period)", re.I)

# Terms that are not in the document you are being asked to sign.
AS_TERMS_ELSEWHERE = re.compile(
    r"(?:(?:as\s+per|following|in\s+line\s+with|subject\s+to)\s+(?:the\s+)?"
    r"(?:company|internal|organisation\w*)\s+[A-Za-z ]{0,25}?polic\w+"
    r"|(?:detailed|set\s+out|specified|governed|described)\s+in\s+the\s+[A-Za-z ]{3,40}\s*agreement"
    r"|in\s+accordance\s+with\s+(?:the\s+)?(?:company|internal)\s+polic\w+"
    r"|will\s+be\s+communicated\s+(?:at\s+the\s+time|later|separately|in\s+due\s+course)"
    r"|which\s+may\s+be\s+(?:updated|amended|revised)\s+from\s+time\s+to\s+time)", re.I)

AS_MATERIAL_TERM = re.compile(
    r"(?:compensation|salary|remuneration|ctc|benefit|leave|assessment|evaluation"
    r"|incentive|bonus|appraisal|notice)", re.I)

# Confidentiality with no end and no carve-out for what becomes public anyway.
AS_PERPETUAL_CONF = re.compile(
    r"(?:during\s+and\s+after|at\s+all\s+times|in\s+perpetuity|shall\s+survive"
    r"|even\s+after\s+(?:the\s+)?(?:termination|cessation|expiry))"
    r"[^.]{0,120}?confidential|confidential[^.]{0,120}?"
    r"(?:during\s+and\s+after|in\s+perpetuity|shall\s+survive)", re.I)

AS_PUBLIC_CARVEOUT = re.compile(
    r"(?:public\s+domain|publicly\s+(?:available|known)|already\s+known"
    r"|becomes\s+public|through\s+no\s+fault|independently\s+developed"
    # Limiting the duty to what is not public is the same carve-out said the other
    # way round: "keep confidential all non-public information" was flagged as
    # having no carve-out for public information.
    r"|\bnon[-\s]?public\b|not\s+(?:otherwise\s+)?(?:publicly|generally)\s+(?:available|known)"
    r"|generally\s+(?:available|known)\s+to\s+the\s+public)", re.I)

# Termination with nothing the person can do about it first.
AS_IMMEDIATE_TERMINATION = re.compile(
    r"(?:terminat\w+|end\s+(?:it|this|your)\b|withdraw\w*\s+(?:the\s+)?offer|dismiss\w*)"
    r"[^.]{0,160}?"
    r"(?:with\s+immediate\s+effect|without\s+notice|sole\s+discretion|forthwith)"
    r"|(?:with\s+immediate\s+effect|sole\s+discretion)[^.]{0,160}?"
    r"(?:terminat\w+|withdraw\w*\s+(?:the\s+)?offer)", re.I)

# Immediate termination for sexual harassment is not a drafting defect.
#
# On a real internship offer letter, R-EMP-10 fired on the POSH clause because its
# note ended "it would result in termination with immediate effect", and this reader
# matched that phrase with no idea what the termination was for. The negotiation
# draft then asked the employer to narrow its harassment policy and give interns a
# cure period for harassment. Sent under the candidate's name, that letter would do
# them real harm, and it asks for something no employer should agree to.
#
# So the reader asks what the clause is ABOUT before calling its termination
# language unfair. A clause whose subject is sexual harassment is left alone, unless
# it ALSO terminates on grounds that are genuinely vague, in which case the vague
# grounds are still a fair flag. "Performance not up to standards", "prejudicial to
# the interest" and "sole discretion" are the complaint R-EMP-10 exists to make;
# harassment never is.
AS_POSH_SUBJECT = re.compile(
    r"(?:sexual\s+harass\w*|harassment\s+(?:at|in)\s+(?:the\s+)?work\s*place"
    r"|\bPOSH\b|internal\s+complaints?\s+committee|prevention\s+of\s+sexual)", re.I)

AS_VAGUE_GROUND = re.compile(
    # The noun, not the verb. "\\bperform\\w*" matched "activities performed at any
    # other site" inside the harassment clause's own definition of the workplace, and
    # that one word turned a harassment policy back into a broad-cause finding.
    r"(?:\b(?:non-?)?performance\b|under-?perform\w*|not\s+up\s+to|unsatisfactor\w*"
    r"|prejudicial|harmful\s+to\s+the\s+interest"
    r"|misbehav\w*|sole\s+(?:opinion|discretion)|for\s+any\s+reason|without\s+assigning"
    r"|inconsistent\s+with\s+the\s+polic\w*|absent\w*|attitude|loss\s+of\s+confidence)", re.I)


def protected_misconduct(as_text):
    """
    True when a clause's subject is sexual harassment and it names no vague ground.

    Used in two places, because a termination fact can arrive by two roads: this
    reader, and the model. Both have to be stopped, or the one left open becomes the
    route by which the same harmful draft is produced.
    """
    a_t = as_text or ""
    return bool(AS_POSH_SUBJECT.search(a_t)) and not AS_VAGUE_GROUND.search(a_t)


# Termination fields that must never be asserted about a harassment clause.
AS_TERMINATION_FIELDS = {
    "cause_undefined_or_vague", "termination_without_cure",
    "termination_without_cure_period", "termination_at_sole_opinion",
}


AS_CURE = re.compile(
    r"(?:opportunity\s+to\s+(?:remedy|cure|rectify|be\s+heard)|cure\s+period"
    r"|after\s+(?:giving|serving)\s+(?:\w+\s+){0,3}notice\s+to\s+remedy"
    r"|show\s+cause|inquiry|enquiry|improvement\s+plan|\bPIP\b)", re.I)


# A clause about background checks is not a termination-for-cause clause. "Failure
# to submit documents on time, or unsatisfactory check results, may lead to
# withdrawal of the offer or termination of employment, at the Company's sole
# discretion" was reported as cause "broad enough to cover ordinary performance",
# while the same letter's real performance ground sat unflagged in another clause.
AS_VERIFICATION_SUBJECT = re.compile(
    r"(?:(?:background|reference|credential|document)\s+(?:check|verification|screening)\w*"
    r"|check\s+results?|submit\w*\s+(?:the\s+|your\s+|required\s+)?documents"
    r"|documents?\s+(?:listed|required|to\s+be\s+submitted))", re.I)

# Grounds about how someone does the job. "conduct" alone is not one: "the Company
# may conduct background checks" is the verb.
AS_CONDUCT_GROUND = re.compile(
    r"(?:\bperformance\b|under-?perform\w*|polic(?:y|ies)|misconduct|behaviou?r"
    r"|attitude|prejudicial|harmful\s+to\s+the\s+interest|loss\s+of\s+confidence)", re.I)

# Termination for a ground only the employer can judge, in a sentence that gives no
# notice and no chance to put it right. "The Company also reserves the right to
# terminate employment for policy violations or unsatisfactory performance" has no
# "with immediate effect" in it, so the pattern above never saw it.
AS_BROAD_GROUND = re.compile(
    r"(?:(?:unsatisfactory|poor|inadequate|sub-?standard)\s+(?:work\s+)?performance"
    r"|non-?performance|under-?perform\w*|not\s+up\s+to\s+(?:the\s+)?(?:mark|standards?)"
    r"|(?:violation|breach)\w*\s+of\s+(?:any\s+)?(?:the\s+)?(?:company\s+|internal\s+)?polic(?:y|ies)"
    r"|polic(?:y|ies)\s+violations?|prejudicial\s+to|harmful\s+to\s+the\s+interest"
    r"|loss\s+of\s+confidence)", re.I)
# A right to terminate, not the word. "For a period of 24 months following the
# termination of your employment for any reason whatsoever" is a non-compete saying
# when it starts, and matched as a termination ground until the verb was required.
AS_TERMINATE_VERB = re.compile(
    r"\b(?:may|shall|can|will|right\s+to|entitled\s+to)\s+(?:\w+\s+){0,3}?"
    r"(?:terminat\w+|dismiss\w*)\b", re.I)
AS_NOTICE_OR_CURE = re.compile(
    r"(?:notice|days['\u2019]|remedy|cure|rectify|improvement\s+plan|\bPIP\b|warning)", re.I)


def _broad_ground_termination(as_text):
    # Starts on a non-space, or the evidence snaps back to the previous sentence's last
    # word and a finding opens "certificate. The Company also reserves...".
    for a_sent in re.finditer(r"[^.;\s][^.;]*[.;]?", as_text or ""):
        a_s = a_sent.group(0)
        if (AS_TERMINATE_VERB.search(a_s) and AS_BROAD_GROUND.search(a_s)
                and not AS_NOTICE_OR_CURE.search(a_s)):
            return a_sent
    return None


def _weekly_hours(as_text):
    """
    Hours a week, from a days range and a times range in the same clause.

    Indian offer letters state this as "Monday - Saturday" and "11:00AM - 8:00PM"
    and leave the reader to multiply. Most people never do. State shops and
    establishments Acts generally cap the week at 48 hours, so a letter can put a
    54-hour week in plain sight and nobody notices.
    """
    a_days = AS_DAY_SPAN.search(as_text)
    a_time = AS_TIME_SPAN.search(as_text)
    if not (a_days and a_time):
        return None, None

    try:
        i, j = AS_DAYS.index(a_days.group(1).lower()), AS_DAYS.index(a_days.group(2).lower())
    except ValueError:
        return None, None
    n_days = (j - i) % 7 + 1

    h1, m1, ap1, h2, m2, ap2 = a_time.groups()
    h1, m1, h2, m2 = int(h1), int(m1), int(h2), int(m2)
    if ap1 and ap1.lower() == "pm" and h1 < 12:
        h1 += 12
    if ap2 and ap2.lower() == "pm" and h2 < 12:
        h2 += 12
    if ap1 and ap1.lower() == "am" and h1 == 12:
        h1 = 0
    # No am/pm marker means a 24-hour clock, which is how "09:30 - 18:30" reads.
    if not ap2 and h2 < h1:
        h2 += 12

    a_hours = (h2 * 60 + m2 - h1 * 60 - m1) / 60.0
    if a_hours <= 0 or a_hours > 16 or n_days > 7:
        return None, None
    return round(n_days * a_hours, 1), a_time


def _weekly_hours_net(as_text):
    """
    The same week less an hour's rest on each day longer than five hours.

    State shops and establishments Acts require a rest interval, commonly half an
    hour to an hour, once five hours have been worked, and their caps count working
    time, not time on the premises. An hour is the generous end of that, so a week
    still over 48 after it is over 48 however the breaks are arranged.
    """
    a_week, a_time = _weekly_hours(as_text)
    if not a_week:
        return None
    a_days = AS_DAY_SPAN.search(as_text)
    i = AS_DAYS.index(a_days.group(1).lower())
    j = AS_DAYS.index(a_days.group(2).lower())
    n_days = (j - i) % 7 + 1
    a_daily = a_week / n_days
    return round(n_days * (a_daily - 1), 1) if a_daily > 5 else a_week


def read_more_facts(doc_type, as_text):
    """The second group of facts. Employment only, for now."""
    as_params, as_evidence = {}, {}
    if doc_type != "employment":
        return as_params, as_evidence

    def put(key, match, value=True):
        ev = _span(match, as_text)
        if ev:
            as_params[key] = value
            as_evidence[key] = ev

    a_week, a_match = _weekly_hours(as_text)
    if a_week:
        as_params["weekly_hours"] = a_week
        as_evidence["weekly_hours"] = _span(a_match, as_text)
        as_params["weekly_hours_net"] = _weekly_hours_net(as_text)
        as_evidence["weekly_hours_net"] = as_evidence["weekly_hours"]

    # Settlement or an experience certificate used as leverage. Both have to be
    # in the same clause, or a letter that mentions a settlement anywhere and a
    # condition anywhere would trip it.
    a_lev = AS_SETTLEMENT_LEVERAGE.search(as_text)
    if a_lev and AS_CONDITIONAL.search(as_text):
        put("settlement_conditional", a_lev)

    a_unpaid = AS_UNPAID.search(as_text)
    if a_unpaid and AS_WORK_PERIOD.search(as_text):
        put("unpaid_work_period", a_unpaid)

    a_ref = AS_TERMS_ELSEWHERE.search(as_text)
    if a_ref and AS_MATERIAL_TERM.search(as_text):
        put("material_terms_elsewhere", a_ref)

    a_conf = AS_PERPETUAL_CONF.search(as_text)
    if a_conf and not AS_PUBLIC_CARVEOUT.search(as_text):
        put("perpetual_confidentiality", a_conf)
        # R-EMP-12 has read these two fields since it was written. Nothing was
        # ever setting them, so the rule could not fire on any document.
        put("perpetual", a_conf)
        as_params["excludes_public_information"] = False
        as_evidence["excludes_public_information"] = as_evidence["perpetual"]

    a_term = AS_IMMEDIATE_TERMINATION.search(as_text)
    if (a_term and AS_VERIFICATION_SUBJECT.search(as_text)
            and not AS_CONDUCT_GROUND.search(as_text)):
        a_term = None
    if not a_term:
        a_term = _broad_ground_termination(as_text)
    if a_term and not AS_CURE.search(as_text) and not protected_misconduct(as_text):
        put("termination_without_cure", a_term)
        put("cause_undefined_or_vague", a_term)   # the field R-EMP-10 reads

    return as_params, as_evidence


# ---------------------------------------------------------------------------
# Topic gates
# ---------------------------------------------------------------------------
#
# A weaker cousin of DETERMINISTIC_ONLY, for fields the model must still read but
# must not be allowed to read into a clause that is not about them.
#
# What went wrong without this
# ----------------------------
# Three failures, all the same shape, all found on real documents:
#
#   An internship offer letter was audited as a loan agreement. The model was
#   handed clause 10.1, where the candidate confirms his educational
#   qualifications, and returned acceleration_without_notice. R-LOAN-06 fired and
#   told the reader the entire balance could be called in with no notice. There is
#   no balance.
#
#   A rent agreement's administrative-charges clause, which lets the landlord
#   revise document-processing fees on fifteen days' notice, produced
#   escalation_undefined. R-RENT-09 reported it as an uncapped rent revision. Rent
#   is fixed elsewhere in that agreement and the clause never mentions it. The
#   negotiation draft then opened "The clause on rent revision reads" and quoted
#   the admin-charges text.
#
#   The same letter produced assignment_without_notice and
#   jurisdiction_lender_city_only from a clause about absence without permission.
#
# In every case the rule was right and the extraction was wrong. Tightening the
# rules would have been the wrong repair: a rule cannot tell whether the fact it
# was handed came off the page.
#
# What a gate is
# --------------
# A field is trusted only if the clause contains at least one word from the
# vocabulary that field is about. No word, no fact, and the trace records it.
#
# The vocabularies are deliberately wide. A gate that is too tight suppresses a
# true finding and that is the expensive failure, so each one lists every ordinary
# way of saying the thing rather than the phrasing that happened to appear in a
# test document. Nothing here is keyed to a particular contract; a field with no
# entry passes through untouched.
AS_GATES = {
    # --- rental -----------------------------------------------------------
    # These two are about RENT going up, so the gate asks for rent, not for any
    # verb meaning "change". With "revis" in the list, an administrative-charges
    # clause that lets the landlord revise document-processing fees passed the
    # gate and was reported to the reader as an uncapped rent revision.
    "escalation_undefined": [
        r"\brent\b|lease\s+amount|licen[cs]e\s+fee|monthly\s+(?:consideration|payment|amount)",
        r"escalat|increas|revis|enhanc|hike|adjust|review|re-?fix",
    ],
    "escalation_percent": [
        r"\brent\b|lease\s+amount|licen[cs]e\s+fee|monthly\s+(?:consideration|payment|amount)",
        r"escalat|increas|revis|enhanc|hike|adjust|review|re-?fix",
    ],
    "lock_in_binds_tenant_only": r"lock[-\s]?in|minimum\s+(?:period|commitment|term|tenure)|shall\s+not\s+vacate|premature",
    "early_exit_forfeits_full_deposit": r"deposit|vacat|leave|quit|premature|lock[-\s]?in",
    "deposit_months": r"deposit|advance|caution\s+money|security",
    "forfeiture_at_sole_discretion": r"deposit|forfeit|withhold|refund|advance|security",
    "deposit_forfeited_entirely_on_any_breach": r"deposit|forfeit|breach|security",
    "forfeiture_disproportionate_to_breach": r"deposit|forfeit|breach|damages|compensation",
    "entry_without_notice": r"enter|entry|inspect|access|visit|premises",
    "entry_notice_hours": r"enter|entry|inspect|access|visit|premises",
    "entry_causes_unspecified": r"enter|entry|inspect|access|visit|premises",
    "structural_repairs_on_tenant": r"repair|maintenance|upkeep|structural|whitewash|plumb|leak",
    "auto_renews_on_landlord_terms": r"renew|extend|continuation|further\s+term",
    "jurisdiction_away_from_premises": r"jurisdiction|court|tribunal|forum|venue|arbitrat|dispute",
    "premises_type": r"premises|property|flat|apartment|house|shop|office|residential|commercial",
    "registered": r"registrat|registered|sub[-\s]?registrar|stamp\s+dut",
    "term_months": r"term|period|tenure|tenancy|commenc|expir|duration",

    # --- loan -------------------------------------------------------------
    "penal_interest_added_to_rate": r"penal|penalt|late\s+(?:charge|fee|payment)|default|overdue|delay",
    "penal_charges_capitalised": r"penal|penalt|capitalis|capitaliz|compound|late\s+charge|overdue",
    "individual_penal_higher_than_corporate": r"penal|penalt|individual|retail|corporate|borrower",
    "penal_charge_amount_undisclosed": r"penal|penalt|late\s+(?:charge|fee|payment)|default\s+(?:charge|interest)|overdue",
    "stipulated_penalty_amount": r"penal|penalt|liquidated|forfeit|damages|charge|fee",
    "penalty_disproportionate_to_loss": r"penal|penalt|liquidated|forfeit|damages|compensation",
    "acceleration_without_notice": r"accelerat|immediately\s+due|due\s+and\s+payable|recall|entire\s+outstanding|whole\s+of\s+the|event\s+of\s+default|forthwith",
    "rate_reset_at_sole_discretion": r"interest|rate|benchmark|repric|reset|revis|spread|margin",
    "set_off_across_unrelated_accounts": r"set[-\s]?off|lien|adjust|appropriat|other\s+account|credit\s+balance",
    "set_off_without_notice": r"set[-\s]?off|lien|adjust|appropriat|other\s+account|credit\s+balance",
    "guarantee_uncapped": r"guarant|surety|indemnifier|co[-\s]?borrower",
    "prepayment_penalty": r"pre[-\s]?pay|pre[-\s]?clos|foreclos|early\s+repay|part[-\s]?payment",
    "insurance_mandatory_named_insurer": r"insur|policy|cover|premium",
    "assignment_without_notice": [
        r"assign|transfer|securitis|securitiz|novat|sell\s+the\s+loan",
        r"\bright|\bloan\b|agreement|receivable|transferee|assignee|securitis|securitiz|participation",
    ],
    "repossession_without_notice": r"reposs|seiz|take\s+possession|enforce\s+the\s+security|hypothecat|recovery\s+agent",
    "jurisdiction_lender_city_only": r"jurisdiction|court|tribunal|forum|venue|arbitrat|dispute",

    # --- employment -------------------------------------------------------
    # Wider than the other two, because the employment path is the one with a
    # measured baseline and a gate that is too keen here would cost recall that
    # was expensive to win.
    "post_termination_restraint": r"compet|solicit|restrain|restrict|after\s+(?:the\s+)?(?:termination|cessation)|following\s+(?:the\s+)?termination|non[-\s]?compete",
    "restricted_from_competing_or_soliciting": r"compet|solicit|engage|employ|restrain|restrict|business",
    "in_term_restraint_only": r"compet|solicit|restrain|restrict|during\s+(?:your|the)\s+employment|last\s+working\s+day",
    "restraint_months": r"compet|solicit|restrain|restrict|period|month|year",
    "ip_assigned_outside_work": r"intellectual|invention|copyright|patent|work\s+product|creat|design|author",
    "moral_rights_assigned": r"moral\s+right|intellectual|copyright|author",
    "prior_work_carve_out": r"intellectual|invention|prior|pre[-\s]?existing|owned\s+before|copyright",
    "notice_asymmetric": r"notice|resign|terminat|separation|leav",
    "notice_days_employee": r"notice|resign|terminat|separation",
    "notice_days_employer": r"notice|resign|terminat|separation",
    "termination_at_sole_opinion": r"terminat|dismiss|discharge|sole\s+(?:opinion|discretion)|services",
    "confidentiality_perpetual": r"confidential|proprietary|secret|disclos",
    "confidentiality_covers_public_info": r"confidential|proprietary|secret|disclos|public",
    "arbitrator_appointed_by_one_side": r"arbitrat|dispute|tribunal|umpire",
    "probation_extendable_indefinitely": r"probation|confirm|trainee|training\s+period",
    "clawback_on_resignation": r"clawback|recover|refund|repay|bonus|variable|incentive|joining",

    # The remaining employment fields, added after tools/lint_rules.py showed that
    # twenty-one model-read fields had no gate at all. An ungated field can be
    # asserted about any clause in the document, which is how three loan facts were
    # asserted about an offer letter. These are wide by design: the employment path
    # is the one with a measured baseline, and a gate that is too tight here costs
    # recall that was expensive to win.
    "bond_present": r"bond|minimum\s+(?:period|service|tenure|term)|shall\s+serve|commitment|undertaking",
    "compels_continued_service": r"bond|minimum\s+(?:period|service|tenure|term)|shall\s+serve|continue\s+in|remain\s+in",
    "liquidated_damages_stated": r"liquidated|damages|penalty|forfeit|compensation|recover|sum\s+of|shall\s+pay",
    "penalty_amount_stated": r"penalt|liquidated|damages|forfeit|recover|sum\s+of|shall\s+pay|bond",
    "recovery_on_early_exit": r"recover|refund|repay|reimburse|forfeit|resign|leav|quit|terminat|discontinu|early",
    "training_cost_clawback": r"training|clawback|recover|refund|reimburse|investment|cost\s+of",
    "assigns_ip": r"intellectual|invention|copyright|patent|trademark|work\s+product|design|author|creat|assign",
    "assignment_scope_unlimited": r"intellectual|invention|copyright|patent|work\s+product|assign|all\s+work",
    "covers_off_premises": r"intellectual|invention|copyright|work\s+product|premises|outside|off\s+the|away\s+from",
    "covers_outside_work_hours": r"intellectual|invention|copyright|work\s+product|hours|outside|after\s+work|personal\s+time",
    "covers_anything_related_to_business": r"intellectual|invention|copyright|work\s+product|business|related\s+to|field\s+of",
    "prior_work_carveout": r"intellectual|invention|copyright|prior|pre[-\s]?existing|owned\s+before|already",
    "assigns_moral_rights": r"moral\s+right|intellectual|copyright|author|integrity|paternity|waiv",
    "cause_undefined_or_vague": r"terminat|dismiss|discharge|cause|ground|reason|misconduct|sole\s+(?:opinion|discretion)",
    "excludes_public_information": r"confidential|proprietary|secret|disclos|public|generally\s+known",
    "perpetual": r"confidential|proprietary|secret|perpetu|in\s+perpetuity|without\s+limit|indefinit|forever|survive",
    "probation_extendable_without_limit": r"probation|confirm|trainee|training\s+period|extend",
    "termination_without_cure_period": r"terminat|breach|cure|remedy|rectif|notice|default",
}

# A gate is either one pattern, or a list of patterns all of which must match.
#
# One pattern was not enough for two fields. "escalation_undefined" is about rent
# going up, and a security deposit clause that says "equal to two months rent"
# mentions rent without being about changing it, so a single rent-word gate let
# the model call a deposit clause an uncapped rent revision. Likewise
# "assignment_without_notice" is about a lender transferring the loan, and an
# offer letter saying "duties reasonably assigned to you" matched the word assign.
#
# Both are the same shape: the subject and the action have to be present together.
AS_GATES_COMPILED = {
    k: ([re.compile(x, re.I) for x in v] if isinstance(v, list)
        else [re.compile(v, re.I)])
    for k, v in AS_GATES.items()
}


def apply_gates(doc_type, as_text, as_params):
    """
    Drop any model-supplied fact whose clause does not talk about that subject.

    Returns (kept, dropped). `dropped` is a list of (field, value) so the trace can
    say what was thrown away and why, rather than a finding simply not appearing
    and nobody being able to tell whether the rule or the reading was at fault.

    A field with no gate is left alone. So is a falsy value: the gates exist to
    stop a fact being asserted about a clause that does not support it, and
    "this clause does not do that" asserts nothing.
    """
    as_kept, as_dropped = {}, []
    a_protected = protected_misconduct(as_text)

    for a_field, a_value in (as_params or {}).items():
        # A termination fact about a harassment clause is dropped whatever the topic
        # gate says, because the gate only asks "is this about termination", and a
        # harassment clause plainly is.
        if a_protected and a_field in AS_TERMINATION_FIELDS and a_value not in (None, False, 0, ""):
            as_dropped.append((a_field, a_value))
            continue
        a_gate = AS_GATES_COMPILED.get(a_field)
        if a_gate is None or a_value in (None, False, 0, ""):
            as_kept[a_field] = a_value
            continue
        if all(g.search(as_text or "") for g in a_gate):
            as_kept[a_field] = a_value
        else:
            as_dropped.append((a_field, a_value))

    return as_kept, as_dropped
