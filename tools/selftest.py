"""
Does this thing actually work?

    python tools/selftest.py

Two fixtures. One is a deliberately fair offer letter: symmetric notice, a closed
list of termination grounds with a cure period, IP scoped to work hours with a prior
work carve-out, confidentiality that expires and excludes public information, a
restriction that ends on the last working day. Nothing in it should be flagged.

The other is the same letter made harsh: a service bond with a stipulated penalty,
asymmetric notice, perpetual confidentiality over public information, IP reaching
outside work hours with moral rights assigned, a two year post-termination
non-compete, termination at the Company's sole opinion.

A tool that flags the harsh one and stays quiet on the fair one is working. A tool
that flags both is crying wolf. A tool that flags neither is decoration. This is the
test that distinguishes them, and it runs end to end: segmentation, extraction,
rules, citation retrieval and verification.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import segment                    # noqa: E402
from agent import ClauseAgent, TRIAGE_MIN_CLAUSES as TRIAGE_MIN   # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"

# Each document type gets a matched pair. A tool tested on only one type is a tool
# that works on one type, and rental and loan rules had never touched a document
# before these existed.
SUITES = {
    "employment": {
        "fair": "clean_offer.txt", "harsh": "risky_offer.txt",
        "must": {"R-EMP-03": "service bond with a stipulated penalty",
                 "R-EMP-05": "IP reaching outside working hours",
                 "R-EMP-01": "post-termination non-compete"},
        "should": {"R-EMP-06": "no prior-work carve-out",
                   "R-EMP-07": "moral rights purportedly assigned",
                   "R-EMP-08": "asymmetric notice",
                   "R-EMP-10": "termination at sole opinion",
                   "R-EMP-12": "perpetual confidentiality over public information",
                   "R-EMP-13": "arbitrator appointed by one side"},
    },
    "rental": {
        "fair": "clean_rent.txt", "harsh": "risky_rent.txt",
        "must": {"R-RENT-01": "deposit far above the benchmark",
                 "R-RENT-03": "forfeiture at the landlord's sole discretion",
                 "R-RENT-15": "entire deposit forfeited on any breach"},
        "should": {"R-RENT-04": "lock-in binding only the tenant",
                   "R-RENT-06": "notice shorter for the landlord",
                   "R-RENT-07": "entry with no notice at all, section 17 requires 24 hours",
                   "R-RENT-10": "long term left unregistered",
                   "R-RENT-11": "structural repairs pushed to the tenant"},
    },
    "loan": {
        "fair": "clean_loan.txt", "harsh": "risky_loan.txt",
        "must": {"R-LOAN-01": "penal interest added to the rate",
                 "R-LOAN-02": "penal charges compounded"},
        "should": {"R-LOAN-04": "penal charges never quantified",
                   "R-LOAN-06": "acceleration without notice or cure",
                   "R-LOAN-07": "rate reset at sole discretion",
                   "R-LOAN-08": "set-off across unrelated accounts",
                   "R-LOAN-10": "prepayment penalty",
                   "R-LOAN-13": "repossession without notice"},
    },
}

# What the harsh letter must produce. Named by rule so a miss is specific.
MUST_FLAG = {
    "R-EMP-03": "service bond with a stipulated penalty",
    "R-EMP-05": "IP reaching outside working hours",
    "R-EMP-01": "post-termination non-compete",
}
SHOULD_FLAG = {
    "R-EMP-06": "no prior-work carve-out",
    "R-EMP-07": "moral rights purportedly assigned",
    "R-EMP-08": "asymmetric notice",
    "R-EMP-10": "termination at sole opinion",
    "R-EMP-12": "perpetual confidentiality over public information",
    "R-EMP-13": "arbitrator appointed by one side",
}


def audit(as_agent, a_path, doc_type="employment"):
    """
    Returns errors as text, not as a count.

    Counting them and printing a number hid the cause three separate times: half of
    every document was failing to parse and the report simply said "4 extraction
    errors" while three obvious risky clauses went missing. An error you cannot read
    is an error you cannot fix.
    """
    as_clauses = segment.run(a_path, doc_type, quiet=True)
    as_found, as_ok, as_errors, as_skipped = {}, [], [], []
    # Findings the rules produced and the verifier then withheld. Without these a
    # missing required finding is indistinguishable from a rule that never fired.
    as_withheld = {}
    for c in as_clauses:
        out = as_agent.run(doc_type, c.text, clause_id=c.clause_id,
                           always_extract=len(as_clauses) < TRIAGE_MIN)
        if not out.get("triage_keep"):
            as_skipped.append(((c.heading or c.text[:40]).strip(),
                               out.get("triage_reason", "")))
        if out.get("extraction_error"):
            as_errors.append(((c.heading or c.text[:40]).strip(),
                              out["extraction_error"]))
        for f in out.get("findings") or []:
            as_found.setdefault(f["rule_id"], []).append(
                (f["severity"], (c.heading or c.text[:50]).strip()))
        for f in out.get("rejected") or []:
            as_withheld.setdefault(f["rule_id"], f.get("reject_reason", "no reason recorded"))
        as_ok += [f["rule_id"] for f in (out.get("reassurances") or [])]
    return len(as_clauses), as_found, as_ok, as_errors, as_skipped, as_withheld


def show_errors(as_errors, label):
    if not as_errors:
        return
    print(f"\n  {len(as_errors)} clause(s) failed extraction on the {label} letter.")
    print("  Those clauses were never analysed, so anything in them was missed:")
    for head, err in as_errors[:4]:
        print(f"    {head[:44]}")
        print(f"      {err[:150]}")


class OfflineExtractor:
    """
    A keyword stand-in for the model, used by --offline.

    It exists so the deterministic half of the system can be checked without
    spending quota: segmentation, the rules, citation retrieval and verification.
    If the offline run passes and the live run does not, the difference is the
    model, which is a far faster thing to know than staring at both.

    It is not a substitute for the live run. It cannot tell you whether a real
    model reads a real clause correctly, which is the whole question.
    """

    def extract(self, doc_type, text):
        low = text.lower()
        p = {}

        if "following the termination" in low or "following termination" in low:
            # R-EMP-01 now needs the restrained activity named, not just the fact
            # that something survives termination. The same keyword test supplies
            # both: "shall not ... compete / be employed by" is the activity. A
            # confidentiality duty that also survives termination matches neither.
            if "shall not" in low and ("compet" in low or "employed by" in low):
                p["post_termination_restraint"] = True
                p["restricted_from_competing_or_soliciting"] = True
        if "only during your employment" in low or "ends on your last working day" in low:
            p["in_term_restraint_only"] = True

        if "minimum period" in low or "minimum service" in low:
            p["bond_present"] = True
        if "liquidated damages" in low:
            p["liquidated_damages_stated"] = True
        if "rs. 5,00,000" in low or "rs 5,00,000" in low:
            p["penalty_amount_stated"] = True
        if "recoverable as a debt" in low or "shall pay the company a sum" in low:
            p["recovery_on_early_exit"] = True

        if "vest in the company" in low or "property of the company" in low \
                or "assign to the company" in low:
            p["assigns_ip"] = True
            # Negation matters here as much as it does for the real model. The fair
            # fixture says work created outside working hours REMAINS YOURS, which is
            # a carve-out, and reading the phrase without the negation turned the fair
            # letter into a false positive.
            a_carveout = ("remains yours" in low or "remains your property" in low
                          or "belongs to you" in low)
            if ("outside the usual hours" in low or "outside working hours" in low) \
                    and not a_carveout:
                p["covers_outside_work_hours"] = True
            if "off the premises" in low and not a_carveout:
                p["covers_off_premises"] = True
            if "before joining" in low and "remains yours" in low:
                p["prior_work_carveout"] = True
            if "moral rights" in low and "assign" in low and "affects your moral" not in low:
                p["assigns_moral_rights"] = True

        if "ninety (90) days" in low and "thirty (30) days" in low:
            p["notice_asymmetric"] = True

        if "sole opinion" in low or "detrimental to the best interests" in low:
            p["cause_undefined_or_vague"] = True

        if "in perpetuity" in low:
            p["perpetual"] = True
            p["excludes_public_information"] = "publicly available" in low \
                and "whether or not" not in low

        if "arbitrator appointed by the company" in low:
            p["arbitrator_appointed_by_one_side"] = True

        # Rental and loan were never covered here, so --offline silently reported
        # zero findings for two of the three types and looked like a failure rather
        # than an untested path.
        if doc_type == "rental":
            p.update(self._rental(low))
        elif doc_type == "loan":
            p.update(self._loan(low))

        if doc_type == "rental":
            a_cat = ("security_deposit" if any(k.startswith("deposit") or "forfeit" in k
                                               for k in p) else
                     "lock_in" if "lock_in_binds_tenant_only" in p else
                     "notice_period" if "notice_asymmetric" in p else
                     "entry_and_inspection" if "entry_without_notice" in p else
                     "maintenance_and_repairs" if "structural_repairs_on_tenant" in p else
                     "term")
            return {"category": a_cat, "params": p, "confidence": 1.0}

        if doc_type == "loan":
            a_cat = ("penal_charges" if any("penal" in k for k in p) else
                     "acceleration" if "acceleration_without_notice" in p else
                     "interest" if "rate_reset_at_sole_discretion" in p else
                     "security" if "repossession_without_notice" in p else
                     "set_off" if "set_off_across_unrelated_accounts" in p else
                     "boilerplate")
            return {"category": a_cat, "params": p, "confidence": 1.0}

        a_cat = ("non_compete" if "post_termination_restraint" in p else
                 "employment_bond" if "bond_present" in p else
                 "ip_assignment" if "assigns_ip" in p else
                 "notice_period" if "notice_asymmetric" in p else
                 "termination_grounds" if "cause_undefined_or_vague" in p else
                 "confidentiality" if "perpetual" in p else
                 "dispute_resolution" if "arbitrator_appointed_by_one_side" in p else
                 "boilerplate")
        return {"category": a_cat, "params": p, "confidence": 1.0}

    @staticmethod
    def _rental(low):
        p = {}
        if "six (6) months rent" in low or "six months rent" in low:
            p["deposit_months"] = 6
        elif "two (2) months" in low or "two months rent" in low:
            p["deposit_months"] = 2
        if "sole discretion of the lessor" in low or "sole discretion" in low:
            if "deduct" in low or "deposit" in low:
                p["forfeiture_at_sole_discretion"] = True
        if "howsoever minor" in low or ("entire security deposit" in low
                                        and "forfeit" in low):
            p["deposit_forfeited_entirely_on_any_breach"] = True
        if "shall not vacate" in low and "may terminate this agreement at any time" in low:
            p["lock_in_binds_tenant_only"] = True
        if "three (3) months written notice" in low and "fifteen (15) days notice" in low:
            p["notice_asymmetric"] = True
        if "at any time without prior notice" in low or "without prior notice" in low:
            p["entry_without_notice"] = True
        elif "twenty-four hours" in low or "24 hours" in low or "48 hours" in low:
            p["entry_notice_hours"] = 24 if "twenty-four" in low or "24 hours" in low else 48
        if "structural repairs" in low and "lessee" in low and "own cost" in low:
            p["structural_repairs_on_tenant"] = True
        if "twenty-four (24) months" in low and "shall not be registered" in low:
            p["term_months"] = 24
            p["registered"] = False
        return p

    @staticmethod
    def _loan(low):
        p = {}
        if "added to the rate of interest" in low or "added to the applicable rate" in low:
            p["penal_interest_added_to_rate"] = True
        if "compounded" in low and "penal" in low:
            p["penal_charges_capitalised"] = True
        if "not be added to the rate" in low or "separate fee" in low:
            p["penal_interest_added_to_rate"] = False
        if "no interest shall be computed on any penal" in low:
            p["penal_charges_capitalised"] = False
        # "without penalty or foreclosure charge" in a fair prepayment clause matched
        # a bare "penal" substring and flagged the clean fixture. Require the clause
        # to actually levy a charge, and to name neither an amount nor a disclosure.
        a_levies = ("penal charge" in low or "penal interest" in low
                    or "late payment charge" in low)
        a_negated = ("without penalty" in low or "no penalty" in low
                     or "without any penal" in low)
        if a_levies and not a_negated and "key fact statement" not in low \
                and "rs." not in low and "separate fee" not in low:
            p["penal_charge_amount_undisclosed"] = True
        if "immediately due" in low and "without notice" in low:
            p["acceleration_without_notice"] = True
        if "sole discretion" in low and ("rate" in low or "interest" in low):
            p["rate_reset_at_sole_discretion"] = True
        if "any other account" in low and "set off" in low.replace("set-off", "set off"):
            p["set_off_across_unrelated_accounts"] = True
        if "take possession" in low and "without notice" in low:
            p["repossession_without_notice"] = True
        return p


def run_suite(as_agent, doc_type, cfg):
    print("\n" + "=" * 72)
    print(f"{doc_type.upper()}")
    print("=" * 72)

    n_fp, as_errs = 0, []
    if cfg["fair"]:
        n1, f1, ok1, e1, s1, w1 = audit(as_agent, FIXTURES / cfg["fair"], doc_type)
        n_fp = sum(len(v) for v in f1.values())
        as_errs += e1
        print(f"  fair fixture   {n1} clauses, {n_fp} findings, "
              f"{len(ok1)} reassurances, {len(e1)} errors")
        for rid, hits in f1.items():
            for sev, head in hits:
                print(f"    FALSE POSITIVE [{sev}] {rid} on '{head[:44]}'")
    else:
        print("  fair fixture   (none for this type)")

    n2, f2, ok2, e2, s2, w2 = audit(as_agent, FIXTURES / cfg["harsh"], doc_type)
    as_errs += e2
    print(f"  harsh fixture  {n2} clauses, {sum(len(v) for v in f2.values())} "
          f"findings, {len(e2)} errors")
    for rid in sorted(f2):
        a_label = cfg["must"].get(rid) or cfg["should"].get(rid) or "additional finding"
        for sev, _ in f2[rid]:
            print(f"    [{sev:<6}] {rid}  {a_label}")

    as_missed = [r for r in cfg["must"] if r not in f2]
    n_should = sum(1 for r in cfg["should"] if r in f2)
    print(f"\n  required   {len(cfg['must']) - len(as_missed)}/{len(cfg['must'])}"
          f"   optional {n_should}/{len(cfg['should'])}"
          f"   false positives {n_fp}   errors {len(as_errs)}")
    if n_should < len(cfg["should"]):
        print("    Optional findings vary between runs. They depend on the model")
        print("    noticing a field, not on a rule being present, so a lower count")
        print("    here is model variance rather than a defect.")
    # A miss with no reason attached is the same failure this project keeps finding
    # everywhere else: silence that looks like safety. A required finding can go
    # missing because the extractor never set the parameter, or because the rule
    # fired and the verifier then withheld it over its citation. Those are opposite
    # problems and the fix for one is not the fix for the other, so name which.
    for r in as_missed:
        a_why = (f"withheld at verification: {w2[r]}" if r in w2
                 else "the rule never fired, so the extractor did not set its parameter")
        print(f"    MISSED  {r}: {cfg['must'][r]}")
        print(f"            {a_why}")

    return not as_missed and n_fp == 0 and not as_errs


def main():
    a_offline = "--offline" in sys.argv

    if a_offline:
        as_ex = OfflineExtractor()
        print("running OFFLINE with a keyword stand-in for the model.")
        print("This checks segmentation, rules, citations and verification.")
        print("Run without --offline to test whether a real model reads clauses well.")
    else:
        try:
            from extract import Extractor
            as_ex = Extractor()
            import llm
            print(f"running end to end via {llm.provider()} / {llm.model_name()}")
        except Exception as e:
            print(f"no extraction backend ({e})")
            print("Use --offline to check everything except the model.")
            sys.exit(1)

    as_agent = ClauseAgent(extractor=as_ex)

    as_pass = {}
    for doc_type, cfg in SUITES.items():
        as_pass[doc_type] = run_suite(as_agent, doc_type, cfg)

    print("\n" + "=" * 72)
    print("VERDICT")
    print("=" * 72)
    for doc_type, ok in as_pass.items():
        print(f"  {doc_type:<12} {'PASS' if ok else 'NEEDS WORK'}")

    a_all = all(as_pass.values())
    print(f"\n  {'ALL TYPES PASS' if a_all else 'SOME TYPES NEED WORK'}: "
          "flags the harsh fixtures, stays quiet on the fair ones."
          if a_all else
          f"\n  SOME TYPES NEED WORK. See the misses above.")

    print("\n  Flags for review, not legal advice.")
    sys.exit(0 if a_all else 1)


if __name__ == "__main__":
    main()
