"""
The evaluation harness.

    python tools/evaluate.py --groundedness employment
    python tools/evaluate.py --classification employment
    python tools/evaluate.py --all employment

Three numbers, and they measure different things:

groundedness    of every statutory or policy finding, what share carried a citation
                span that actually opens at the section the rule named. Fully
                automatic, no labels needed, and it is the number that matters most
                for an agentic RAG system.

rejection       what share of candidate findings the verify node threw out. A system
                that never rejects anything is not verifying anything.

classification  category accuracy against the small set of clauses the user
                validated by hand. This is the only part that needs human labels,
                and it needs about forty of them, not three hundred.

Writes data/eval_results.json, which the README quotes.
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from typeregistry import TypeRegistry   # noqa: E402
from schema import read_jsonl        # noqa: E402
from rules import RuleEngine         # noqa: E402
from retrieve import StatuteIndex    # noqa: E402

OUT_PATH = ROOT / "data" / "eval_results.json"


def groundedness(doc_type, sample=None):
    """
    Fire every statutory and policy rule, then check the citation it gets back.

    A finding passes only if a span came back AND the span opens at the section the
    rule named. That second half matters: retrieving something from the right Act is
    not the same as retrieving the right section of it.
    """
    as_engine = RuleEngine()
    as_index = StatuteIndex()
    if not as_index.chunks:
        as_index.build()

    as_checked, as_grounded, as_failures = 0, 0, []

    for rule in as_engine.rules_for(doc_type):
        if rule["basis"] not in ("statutory", "regulatory", "policy"):
            continue
        as_checked += 1

        hit = as_index.cite(rule.get("source"), anchors=rule.get("anchors"))
        if not hit:
            as_failures.append({"rule_id": rule["id"], "reason": "no span retrieved"})
            continue

        section = (rule.get("source") or {}).get("section")
        if section and not re.search(rf"\b{re.escape(str(section))}\b", hit["text"][:120]):
            as_failures.append({"rule_id": rule["id"],
                                "reason": f"span does not open at section {section}"})
            continue

        as_grounded += 1

    rate = as_grounded / as_checked if as_checked else 0.0
    print(f"\ngroundedness, {doc_type}")
    print(f"  statutory, regulatory and policy rules  {as_checked}")
    print(f"  citation resolved and matched  {as_grounded}")
    print(f"  groundedness  {rate:.1%}")
    if as_failures:
        print(f"  {len(as_failures)} could not be grounded:")
        for f in as_failures:
            print(f"    {f['rule_id']}: {f['reason']}")
        print("  these are withheld from reports rather than shown ungrounded")

    return {"checked": as_checked, "grounded": as_grounded,
            "rate": round(rate, 4), "failures": as_failures}


def verification_selftest(doc_type):
    """
    Prove the verification node actually rejects bad citations.

    An earlier version of this check ran the agent with empty parameters, so no rule
    could fire, and it reported a rejection rate of zero out of zero findings. That
    measured nothing while looking like a pass. This injects a deliberately wrong
    citation instead and asserts the verifier throws the finding out, which is a
    real test and needs no API key.
    """
    from agent import ClauseAgent

    as_agent = ClauseAgent()

    # Derive a firing parameter set from the rules rather than hardcoding one per
    # type. A hardcoded map meant adding a document type crashed this check with a
    # KeyError, which is exactly the kind of breakage the type registry exists to
    # prevent, so the test should not reintroduce it.
    as_params = None
    for rule in as_agent.engine.rules_for(doc_type):
        if rule["basis"] == "norm" or "source" not in rule:
            continue
        # A rule with risky false is a reassurance, and the aggregate node moves it
        # out of findings by design, so picking one here asserts that a finding
        # survived when none could ever have been there. This loop used to take the
        # first grounded single-condition rule and R-EMP-01 happened to be both first
        # and risky, so the omission never showed. Tightening R-EMP-01 to two
        # conditions pushed the pick onto R-EMP-02, which is a reassurance, and the
        # test failed on a subject it should never have chosen.
        if not rule.get("risky", rule.get("severity") != "none"):
            continue
        as_when = rule["when"]
        if any(k in as_when for k in ("all", "any")):
            continue
        as_cand = {k: (True if not isinstance(v, dict) else v.get("gt", 0) + 1)
                   for k, v in as_when.items()}
        if as_agent.engine.evaluate(doc_type, as_cand):
            as_params = as_cand
            break

    if as_params is None:
        print(f"\nverification self-test, {doc_type}: no single-condition grounded rule to test with")
        return {"status": "not_measured", "reason": "no testable rule"}

    text = "x" * 200 + " shall not compete"

    clean = as_agent.run(doc_type, text, as_params=as_params)
    n_clean = len(clean.get("findings") or [])

    a_real = as_agent.index.by_section
    a_alias = as_agent.index._alias

    # Three ways a citation can be wrong, not one. The first is a wrong section,
    # which the original check covered. The other two are the failures that actually
    # shipped: a span that opens at the right section but contains only its heading,
    # and a span from the right document that is page furniture rather than law.
    # Both passed an opens-at-the-right-place check while supporting nothing.
    as_injections = {
        "wrong section": {
            "corpus_id": "wrong", "section": "?",
            "text": "999. An entirely unrelated provision about stamp duty on conveyances.",
            "exact": True},
        "heading only": {
            "corpus_id": "right", "section": "74",
            "text": "74. Compensation for breach of contract where penalty stipulated for.",
            "exact": True},
        "boilerplate from the right document": {
            "corpus_id": "right", "section": "74",
            "text": ("74. " + "PRS makes every effort to use reliable and comprehensive "
                     "information, but does not represent that the contents of this "
                     "report are accurate or complete. This document has been prepared "
                     "without regard to the objectives of those who may receive it."),
            "exact": True},
    }

    as_results = {}
    for a_label, a_payload in as_injections.items():
        as_agent.index.by_section = lambda *a, _p=a_payload, **k: _p
        as_agent.index._alias = {}
        a_run = as_agent.run(doc_type, text, as_params=as_params)
        as_results[a_label] = {
            "kept": len(a_run.get("findings") or []),
            "rejected": len(a_run.get("rejected") or []),
        }
        as_agent.index.by_section = a_real
        as_agent.index._alias = a_alias

    for a_label, a_r in as_results.items():
        a_ok = a_r["kept"] == 0 and a_r["rejected"] > 0
        print(f"  {'ok  ' if a_ok else 'FAIL'} {a_label:<38} "
              f"{a_r['kept']} kept, {a_r['rejected']} rejected")

    as_agent.index.by_section = lambda *a, **k: as_injections["wrong section"]
    as_agent.index._alias = {}

    poisoned = as_agent.run(doc_type, text, as_params=as_params)

    as_agent.index.by_section = a_real
    as_agent.index._alias = a_alias

    n_kept = len(poisoned.get("findings") or [])
    n_rejected = len(poisoned.get("rejected") or [])
    ok = n_clean > 0 and n_kept == 0 and n_rejected > 0

    print(f"\nverification self-test, {doc_type}")
    print(f"  with a correct citation   {n_clean} findings kept")
    print(f"  with a poisoned citation  {n_kept} kept, {n_rejected} rejected")
    print(f"  retries attempted         {poisoned.get('retries', 0)}")
    print(f"  result  {'PASS, the verifier rejects unsupported findings' if ok else 'FAIL'}")
    if n_rejected:
        for f in poisoned["rejected"]:
            print(f"    {f['rule_id']}: {f['reject_reason']}")

    return {"clean_findings": n_clean, "poisoned_kept": n_kept,
            "poisoned_rejected": n_rejected, "passed": bool(ok)}


def triage_coverage(doc_type, limit=200):
    """What share of the corpus the router routes past the model."""
    as_rows = []
    for p in sorted((ROOT / "data" / "clauses").glob("*.jsonl")):
        as_rows += [r for r in read_jsonl(p) if r.get("doc_type") == doc_type]
    as_rows = as_rows[:limit]
    if not as_rows:
        return {"error": "no clauses"}

    from agent import ClauseAgent
    as_agent = ClauseAgent()
    # No as_params, so triage actually runs. Passing an empty dict would trip the
    # bypass and report a skip rate of zero.
    n_keep = sum(1 for r in as_rows if as_agent.run(doc_type, r["text"]).get("triage_keep"))

    print(f"\ntriage, {doc_type}, {len(as_rows)} clauses")
    print(f"  kept    {n_keep} ({n_keep/len(as_rows):.0%})")
    print(f"  skipped {len(as_rows)-n_keep} ({1-n_keep/len(as_rows):.0%})")
    return {"clauses": len(as_rows), "kept": n_keep,
            "skipped": len(as_rows) - n_keep,
            "skip_rate": round(1 - n_keep / len(as_rows), 4)}


def wilson(a_hits, a_n, a_z=1.96):
    """
    Wilson score interval for a proportion.

    The textbook normal approximation is wrong at the sample sizes this project
    can afford. At n=40 and 90% accuracy it produces an upper bound above 100%,
    which is not a confidence interval, it is an arithmetic accident. Wilson stays
    inside the unit interval and is well behaved near the ends.
    """
    if a_n == 0:
        return 0.0, 0.0
    p = a_hits / a_n
    a_d = 1 + a_z ** 2 / a_n
    a_centre = p + a_z ** 2 / (2 * a_n)
    a_spread = a_z * ((p * (1 - p) / a_n + a_z ** 2 / (4 * a_n ** 2)) ** 0.5)
    return max(0.0, (a_centre - a_spread) / a_d), min(1.0, (a_centre + a_spread) / a_d)


def classification(doc_type):
    """
    Category accuracy against hand-validated labels.

    Only labels with assisted=False count: a label written with a suggestion on
    screen is not independent evidence about the model.

    This reports how many comparisons actually completed, not just how many labels
    exist. An earlier version counted successes over total labels, so when every
    extraction call failed for want of an API key it printed 0.0 percent accuracy,
    which reads like a devastating result rather than what it was, a metric that
    never ran. A number that cannot be computed is reported as not computed.
    """
    import os

    a_path = ROOT / "data" / "labels" / f"{doc_type}.jsonl"
    if not a_path.exists():
        print(f"\nclassification, {doc_type}: not measured, no labels yet")
        print("  label ~40 clauses in tools/labeler.py to produce this number")
        return {"status": "not_measured", "reason": "no labels"}

    as_labels = [r for r in read_jsonl(a_path) if not r.get("assisted")]
    if len(as_labels) < 10:
        print(f"\nclassification, {doc_type}: not measured, only {len(as_labels)} blind labels")
        return {"status": "not_measured", "reason": f"only {len(as_labels)} labels"}

    as_clauses = {}
    for p in sorted((ROOT / "data" / "clauses").glob("*.jsonl")):
        for r in read_jsonl(p):
            as_clauses[r["clause_id"]] = r

    as_matched = [l for l in as_labels if l["clause_id"] in as_clauses]
    n_orphan = len(as_labels) - len(as_matched)

    if n_orphan:
        print(f"\nclassification, {doc_type}: {n_orphan} of {len(as_labels)} labels are orphaned")
        print("  Those clause ids are not on disk. Clause ids are content-hashed, so")
        print("  re-downloading or re-segmenting after labelling detaches them.")
        if not as_matched:
            print("  Nothing left to compare against. Re-label against the current corpus.")
            return {"status": "not_measured", "reason": "all labels orphaned",
                    "orphaned": n_orphan}

    # This asked for ANTHROPIC_API_KEY by name while llm.py routes to whichever
    # provider LLM_PROVIDER names. On a Groq setup the key check failed even with
    # everything else in place, so the one metric the scope lock still owes was
    # reporting "not measured" for a reason that had nothing to do with labels or
    # with the model. Ask the module that actually makes the call.
    import llm
    a_ready, a_why = llm.available()
    if not a_ready:
        print(f"\nclassification, {doc_type}: not measured, {a_why}")
        print("  This metric needs one extraction call per label.")
        return {"status": "not_measured", "reason": a_why}

    try:
        from extract import Extractor
        as_ex = Extractor()
    except Exception as e:
        print(f"\nclassification, {doc_type}: not measured ({e})")
        return {"status": "not_measured", "reason": str(e)}

    n_ok, n_failed, n_correct = 0, 0, 0
    as_confusion = Counter()
    as_per_cat = {}

    for lab in as_matched:
        c = as_clauses[lab["clause_id"]]
        try:
            got = as_ex.extract(doc_type, c["text"]).get("category")
        except Exception:
            n_failed += 1
            continue
        n_ok += 1
        a_n, a_c = as_per_cat.get(lab["category"], (0, 0))
        if got == lab["category"]:
            n_correct += 1
            as_per_cat[lab["category"]] = (a_n + 1, a_c + 1)
        else:
            as_confusion[f"{lab['category']} -> {got}"] += 1
            as_per_cat[lab["category"]] = (a_n + 1, a_c)

    if n_ok == 0:
        print(f"\nclassification, {doc_type}: not measured, all {n_failed} calls failed")
        return {"status": "not_measured", "reason": "every extraction call failed",
                "attempted": n_failed}

    acc = n_correct / n_ok
    a_lo, a_hi = wilson(n_correct, n_ok)

    # What guessing the commonest category every time would score. Without it a
    # number means nothing: if two thirds of clauses in this type are boilerplate,
    # 66% accuracy is a model that has learned to say boilerplate.
    a_counts = Counter(l["category"] for l in as_matched)
    a_major, a_major_n = a_counts.most_common(1)[0]
    a_baseline = a_major_n / len(as_matched)

    print(f"\nclassification, {doc_type}")
    print(f"  comparisons completed  {n_ok} of {len(as_labels)} labels")
    if n_failed:
        print(f"  calls failed           {n_failed}")
    print(f"  category accuracy      {acc:.1%}   "
          f"95% interval {a_lo:.1%} to {a_hi:.1%}")
    print(f"  always-guess baseline  {a_baseline:.1%}   "
          f"(always answering '{a_major}')")

    a_beats = a_lo > a_baseline
    print(f"  beats that baseline    {'yes' if a_beats else 'NO, the interval '
                                     'overlaps it'}")

    # The interval is the point. At n=40 an 80% result carries about twelve points
    # either way, and reporting the bare number claims a precision the sample does
    # not have. That is the same error as the "unusual vs comparable agreements"
    # line that was removed from the interface for asserting a dataset that did
    # not exist.
    a_width = (a_hi - a_lo) * 100
    if a_width > 20:
        print(f"  [!] the interval is {a_width:.0f} points wide. Report the interval,")
        print(f"      never the bare figure. More labels is the only way to narrow it.")

    if as_per_cat:
        print("  per category, where the model puts them:")
        for a_cat in sorted(as_per_cat):
            a_n, a_c = as_per_cat[a_cat]
            print(f"    {a_cat:<34} {a_c}/{a_n}")

    if as_confusion:
        print("  most common confusions:")
        for k, v in as_confusion.most_common(5):
            print(f"    {k}  ({v})")

    print(f"\n  for SCOPE.md:")
    print(f"  | Category accuracy | {acc:.0%} ({a_lo:.0%} to {a_hi:.0%}, n={n_ok}) "
          f"| Yes, {n_ok} hand labels |")

    return {"status": "measured", "n": n_ok, "orphaned": n_orphan,
            "failed": n_failed, "accuracy": round(acc, 4),
            "ci_low": round(a_lo, 4), "ci_high": round(a_hi, 4),
            "baseline": round(a_baseline, 4), "beats_baseline": a_beats,
            "per_category": {k: list(v) for k, v in as_per_cat.items()},
            "confusions": dict(as_confusion.most_common(10))}



def classification_all():
    """
    One accuracy figure across all three types, plus the per-type breakdown.

    Forty labels split three ways is thirteen per type, and thirteen comparisons
    carries an interval roughly thirty points wide, which is not a measurement of
    anything. The combined figure is the one worth reporting; the per-type rows
    are there so a type that is dragging it down is visible rather than averaged
    away.
    """
    as_each, a_hits, a_n = {}, 0, 0
    a_reason = None

    for a_type in sorted(TypeRegistry().slugs()):
        a_out = classification(a_type)
        as_each[a_type] = a_out
        if a_out.get("status") == "measured":
            a_hits += round(a_out["accuracy"] * a_out["n"])
            a_n += a_out["n"]
        else:
            a_reason = a_out.get("reason")

    print(f"\n{'=' * 72}\nCLASSIFICATION, ALL TYPES\n{'=' * 72}")

    if a_n == 0:
        print(f"  not measured: {a_reason or 'no labels anywhere'}")
        print("\n  To produce this number:")
        print("    python tools/build_queue.py --type employment --n 60")
        print("    python tools/build_queue.py --type rental --n 60")
        print("    python tools/build_queue.py --type loan --n 60")
        print("    python tools/labeler.py --type employment     # and rental, and loan")
        print("  Label the split=test rows. Those are the blind ones and the only")
        print("  ones that count here.")
        return {"status": "not_measured", "reason": a_reason or "no labels"}

    a_acc = a_hits / a_n
    a_lo, a_hi = wilson(a_hits, a_n)

    for a_type, a_out in as_each.items():
        if a_out.get("status") == "measured":
            print(f"  {a_type:<12} {a_out['accuracy']:.1%}  n={a_out['n']:<4}"
                  f"  baseline {a_out['baseline']:.1%}")
        else:
            print(f"  {a_type:<12} not measured ({a_out.get('reason')})")

    print(f"\n  combined     {a_acc:.1%}   95% interval {a_lo:.1%} to {a_hi:.1%}"
          f"   n={a_n}")
    print(f"\n  for SCOPE.md:")
    print(f"  | Category accuracy | {a_acc:.0%} ({a_lo:.0%} to {a_hi:.0%}, n={a_n}) "
          f"| Yes, {a_n} hand labels |")
    print("\n  Report the interval, not the bare figure. It is the honest form of")
    print("  the number, and this project has already removed one claim for")
    print("  asserting more than the evidence supported.")

    return {"status": "measured", "accuracy": round(a_acc, 4), "n": a_n,
            "ci_low": round(a_lo, 4), "ci_high": round(a_hi, 4),
            "per_type": {k: v for k, v in as_each.items()}}


def api_contract_selftest(doc_type="employment"):
    """
    Drive the HTTP endpoint the browser actually calls, on every fixture.

    Everything else in this file tests the agent. Nothing tested the layer between
    the agent and the browser, and that is where a release broke: adding a
    non-optional field to the response model turned every upload into a 500,
    because findings were being built by handing the model an explicit None for
    every key a finding happened not to carry. The agent tests all passed. The
    site did not load.
    """
    from fastapi.testclient import TestClient
    import api as api_mod

    as_client = TestClient(api_mod.app)
    as_docs = [("clean_offer.txt", "employment"), ("risky_offer.txt", "employment"),
               ("clean_rent.txt", "rental"), ("risky_rent.txt", "rental"),
               ("clean_loan.txt", "loan"), ("risky_loan.txt", "loan")]

    print("\napi contract self-test")
    a_ok = True
    for name, dt in as_docs:
        a_path = ROOT / "tests" / "fixtures" / name
        if not a_path.exists():
            continue
        r = as_client.post(f"/audit/upload?doc_type={dt}",
                           files={"file": (name, a_path.read_text(), "text/plain")})
        a_good = r.status_code == 200
        a_ok = a_ok and a_good
        print(f"  {'ok  ' if a_good else 'FAIL'} {name:20} {r.status_code}")
        if not a_good:
            print(f"       {r.text[:200]}")

    # A reassurance carries no from_text key, which is the shape that broke.
    a_bare = {"rule_id": "R-TEST-01", "category": "x", "basis": "norm",
              "severity": "none", "risky": False, "message": "m", "verified": True}
    try:
        api_mod.as_finding(a_bare)
        print("  ok   a finding with no optional keys still builds")
    except Exception as e:
        a_ok = False
        print(f"  FAIL a finding with no optional keys: {type(e).__name__}")

    print(f"\n  result  {'PASS, the endpoint answers on every fixture' if a_ok else 'FAIL'}")
    return {"pass": a_ok}


def determinism_selftest(doc_type="employment", runs=12):
    """
    Run the same clauses many times with a model that answers differently every
    time, and check the findings that matter do not move.

    This is the test for the complaint that started all of it: the same offer
    letter audited twice gave nothing, then a high-severity flag, with the input
    and the code unchanged. Temperature was already zero, so that was not the
    cause. The provider samples, and a rule resting on one boolean the model may
    or may not return inherits that.

    The model here is deliberately hostile: it returns a random subset of
    parameters on every call, which is a harsher version of the real variance.
    Anything that survives twelve runs of that survives a bad day on a free tier.
    """
    import random
    from agent import ClauseAgent
    from deterministic import read_facts

    as_clauses = {
        "bond with money": (
            "Upon confirmation of employment, the role carries a minimum tenure "
            "commitment of 2 years from the date of joining. Candidates who choose "
            "to discontinue employment prior to the completion of this 2-year "
            "commitment will be liable to a recovery of upto Rs. 10,00,000."),
        "service period, no money": (
            "The minimum period of service is three (3) months from the date of "
            "enforcement of this offer letter. The minimum period of service is "
            "not negotiable."),
        "unfilled party name": (
            "We are pleased to offer you an internship with [Company Name] Private "
            "Limited (the \"Company\") in the capacity of AI Engineer Intern."),
        "54 hour week": (
            "Working Days: Monday - Saturday. Work Timings: 11:00AM - 8:00PM. "
            "Designation: Business Operations Associate."),
        "settlement held back": (
            "Serving the notice period is mandatory to receive a full and final "
            "settlement and an experience certificate."),
        "unpaid training": (
            "The On-the-Job Training (OJT) period of 5 days will be unpaid."),
        "fair clause": (
            "Either party may end this engagement by giving thirty days written "
            "notice to the other. The notice period is the same for both parties."),
    }

    class MoodyModel:
        """Returns a random subset of what it saw. A bad day, every day."""
        def extract(self, dt, text):
            pool = {"assigns_ip": True, "prior_work_carveout": False,
                    "notice_days_employee": 30, "probation_months": 3,
                    "bond_present": True, "compels_continued_service": True,
                    "post_termination_restraint": True}
            keep = {k: v for k, v in pool.items() if random.random() < 0.5}
            return {"params": keep, "category": None, "confidence": 0.5}
        __call__ = extract

    print("\ndeterminism self-test")
    print(f"  {runs} runs per clause, against a model that answers at random\n")

    as_agent = ClauseAgent(extractor=MoodyModel())
    a_stable = True

    for label, text in as_clauses.items():
        a_fixed, _ = read_facts(doc_type, text)
        as_seen = []
        for _ in range(runs):
            out = as_agent.run(doc_type, text, always_extract=True)
            # Only the findings the deterministic reader is responsible for. The
            # rest still move with the model, and the docs say so.
            ids = sorted({f["rule_id"] for f in (out.get("findings") or [])
                          if any(k in a_fixed for k in
                                 as_agent.engine.params_for(f["rule_id"], doc_type))})
            as_seen.append(tuple(ids))
        a_same = len(set(as_seen)) == 1
        a_stable = a_stable and a_same
        a_got = ", ".join(as_seen[0]) or "none"
        print(f"  {'ok  ' if a_same else 'MOVED'} {label:24} {a_got:12} "
              f"identical across {runs} runs" if a_same else
              f"  MOVED {label:24} {sorted(set(as_seen))}")

    print(f"\n  result  {'PASS, the grounded findings do not move' if a_stable else 'FAIL'}")
    return {"runs": runs, "stable": a_stable}


def extraction_failure_selftest(doc_type="employment"):
    """
    A document the model could not read must never come back looking clean.

    The verification self-test covers one end of the pipeline: a finding whose
    citation does not support it is withheld. This covers the other end. When
    extraction fails there are no findings at all, so every count is legitimately
    zero, and zero findings with zero high severity reads as a clean contract
    unless something explicitly says the document was never checked.

    That is not hypothetical. On a day when the provider's token quota ran out, a
    real appointment letter came back with every clause unread, every counter at
    zero and every clause bordered green.
    """
    from agent import ClauseAgent

    print("\nextraction failure self-test")

    class DeadExtractor:
        """Stands in for a provider that has stopped answering."""
        def __call__(self, *a, **k):
            raise RuntimeError("rate_limit_exceeded: tokens per day, limit 200000")
        extract = __call__

    as_agent = ClauseAgent(extractor=DeadExtractor())
    as_clauses = ["1. TERM\nThis agreement runs for twenty four months.",
                  "2. DEPOSIT\nThe tenant shall pay six months rent as deposit."]
    as_results, as_errors = [], []
    for c in as_clauses:
        out = as_agent.run(doc_type, c, always_extract=True)
        if out.get("extraction_error"):
            as_errors.append(out["extraction_error"])
        as_results.append(out)

    a_unread = len(as_errors)
    as_findings = sum(len(r.get("findings") or []) for r in as_results)

    as_checks = [
        ("every clause reports an extraction error", a_unread == len(as_clauses)),
        ("no findings are invented", as_findings == 0),
        ("the response would be marked unavailable", a_unread > 0),
    ]

    # Partial failure, which is the common case and was the untested one. A quota
    # that runs out mid document leaves some clauses read and some not.
    #
    # This half of the test did not exist, and that is why the defect it now covers
    # reached the browser: a letter with 2 of 13 clauses unread showed a tile saying
    # 2, hatched all 13 clauses as not checked, printed the verdict written for a
    # document where nothing had been read, and still listed a clause it had checked
    # and found acceptable. The cause was one flag, extraction_available, answering
    # both "is there a backend" and "did every clause succeed". The interface reads a
    # false as the first and it was being set by the second.
    class FlakyExtractor:
        """A provider that stops answering partway through a document."""
        def __init__(self, fail_on):
            self.fail_on, self.seen = set(fail_on), -1

        def extract(self, doc_type, text):
            self.seen += 1
            if self.seen in self.fail_on:
                raise RuntimeError("rate_limit_exceeded: tokens per day, limit 200000")
            return {"params": {}, "category": None, "confidence": 0.9}
        __call__ = extract

    as_many = [f"{i}. CLAUSE {i}\nOrdinary contract prose for clause {i}."
               for i in range(6)]
    a_flaky = ClauseAgent(extractor=FlakyExtractor(fail_on=[2, 4]))
    as_part, p_errors = [], []
    for c in as_many:
        out = a_flaky.run(doc_type, c, always_extract=True)
        if out.get("extraction_error"):
            p_errors.append(out["extraction_error"])
        as_part.append(out)

    # The two numbers the interface renders from. api.py derives availability from
    # whether an extractor exists, and nothing else; partiality is carried by the
    # count, which cannot disagree with itself about its own size.
    p_available = a_flaky.extractor is not None
    p_unread = len(p_errors)
    p_hatched = sum(1 for r in as_part if r.get("extraction_error"))

    as_checks += [
        ("a partial failure still reports a backend", p_available is True),
        ("the unread count matches the clauses that failed", p_unread == 2),
        ("the count equals the clauses the interface marks unread",
         p_unread == p_hatched),
        ("a partial failure is not reported as a total one",
         p_unread < len(as_many)),
        ("clauses that were read still produce no invented findings",
         sum(len(r.get("findings") or []) for r in as_part) == 0),
    ]

    for label, okv in as_checks:
        print(f"  {'ok  ' if okv else 'FAIL'} {label}")

    a_ok = all(v for _, v in as_checks)
    print("  result  " + ("PASS, an unread document cannot report a clean verdict"
                          if a_ok else "FAIL"))
    return {"unread": a_unread, "findings": as_findings,
            "partial_unread": p_unread, "partial_of": len(as_many), "pass": a_ok}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--groundedness", metavar="DOC_TYPE")
    ap.add_argument("--verification", metavar="DOC_TYPE")
    ap.add_argument("--triage", metavar="DOC_TYPE")
    ap.add_argument("--classification", metavar="DOC_TYPE")
    ap.add_argument("--classification-all", action="store_true",
                    help="category accuracy across all three types, combined")
    ap.add_argument("--determinism", metavar="DOC_TYPE")
    ap.add_argument("--api", metavar="DOC_TYPE")
    ap.add_argument("--all", metavar="DOC_TYPE")
    a = ap.parse_args()

    as_out = {}
    if a.all:
        as_out = {"doc_type": a.all,
                  "groundedness": groundedness(a.all),
                  "verification": verification_selftest(a.all),
                  "triage": triage_coverage(a.all),
                  "classification": classification(a.all),
                  "determinism": determinism_selftest(a.all),
                  "api": api_contract_selftest(a.all)}
    elif a.groundedness:
        as_out = {"groundedness": groundedness(a.groundedness)}
    elif a.verification:
        as_out = {"verification": verification_selftest(a.verification)}
        as_out["extraction_failure"] = extraction_failure_selftest(a.verification)
        as_out["determinism"] = determinism_selftest(a.verification)
        as_out["api"] = api_contract_selftest(a.verification)
    elif a.triage:
        as_out = {"triage": triage_coverage(a.triage)}
    elif getattr(a, "classification_all", False):
        as_out = {"classification_all": classification_all()}
    elif a.classification:
        as_out = {"classification": classification(a.classification)}
    elif a.determinism:
        as_out = {"determinism": determinism_selftest(a.determinism)}
    elif a.api:
        as_out = {"api": api_contract_selftest(a.api)}
    else:
        ap.print_help(); sys.exit(0)

    as_all = json.loads(OUT_PATH.read_text()) if OUT_PATH.exists() else {}
    as_all.update(as_out)
    OUT_PATH.write_text(json.dumps(as_all, indent=2))
    print(f"\nsaved -> {OUT_PATH.relative_to(ROOT)}")
