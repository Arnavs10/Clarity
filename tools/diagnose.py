"""
Why did this document produce no findings?

    python tools/diagnose.py data/raw/employment_07.txt employment

Zero findings is ambiguous. It can mean the contract is genuinely clean, or that
extraction is returning nothing, or that a citation could not be resolved and every
finding was withheld. Those are very different situations and the report looks the
same in all three, which is the worst property a safety tool can have.

This walks one document and prints what actually happened at each stage: what the
model returned, which parameters survived, which rules fired, and which findings the
verifier threw out.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import segment                          # noqa: E402
import llm                              # noqa: E402
from rules import RuleEngine            # noqa: E402
from retrieve import StatuteIndex       # noqa: E402
from typeregistry import TypeRegistry   # noqa: E402


def check_corpus(doc_type, as_engine, as_index):
    print("\n1. CORPUS AND CITATIONS")
    as_reg = TypeRegistry()
    print(f"   statutes indexed: {len(set(c['corpus_id'] for c in as_index.chunks))}")
    print(f"   chunks: {len(as_index.chunks)}")

    n_ok = n_bad = 0
    for rule in as_engine.rules_for(doc_type):
        if rule["basis"] == "norm":
            continue
        hit = as_index.cite(rule.get("source"), anchors=rule.get("anchors"))
        if hit:
            n_ok += 1
        else:
            n_bad += 1
            print(f"   [!] {rule['id']} cannot be cited: "
                  f"{rule.get('source', {}).get('corpus_id')} not resolvable")
    print(f"   grounded rules citable: {n_ok}, unciteable: {n_bad}")
    if n_bad:
        print("   -> findings from those rules will be withheld, not shown")
    return n_bad


def check_provider():
    print("\n2. MODEL PROVIDER")
    ok, why = llm.available()
    print(f"   {why}")
    if not ok:
        print("   -> no extraction will happen, so no rule can fire")
    return ok


def walk(a_path, doc_type, limit, verbose):
    as_engine = RuleEngine()
    as_index = StatuteIndex()
    if not as_index.chunks:
        as_index.build()

    print("=" * 72)
    print(f"DIAGNOSING  {Path(a_path).name}  as {doc_type}")
    print("=" * 72)

    n_bad = check_corpus(doc_type, as_engine, as_index)
    ok = check_provider()

    print("\n3. SEGMENTATION")
    as_clauses = segment.run(a_path, doc_type, quiet=True)
    print(f"   clauses: {len(as_clauses)}")
    if not as_clauses:
        print("   -> nothing to analyse. The segmenter found no clauses.")
        return
    as_sizes = [len(c.text) for c in as_clauses]
    print(f"   sizes: min {min(as_sizes)}, max {max(as_sizes)}, "
          f"avg {sum(as_sizes)//len(as_sizes)}")

    if not ok:
        print("\nStopping: extraction is unavailable, so the rest cannot be tested.")
        return

    # Take an even spread rather than the first N. On a real filing the opening
    # clauses are the EDGAR header and the recitals, so a head sample reports
    # "nothing extracted" on a document whose interesting clauses sit at position 20.
    if len(as_clauses) <= limit:
        as_sample = as_clauses
    else:
        a_step = len(as_clauses) / limit
        as_sample = [as_clauses[int(i * a_step)] for i in range(limit)]
    print(f"\n4. EXTRACTION ON {len(as_sample)} CLAUSES "
          f"(spread across all {len(as_clauses)})")
    print(f"   fields the rules can read: {len(as_engine.required_params(doc_type))}")

    from extract import Extractor
    as_ex = Extractor()

    n_failed = 0
    as_params_seen = Counter()
    as_cats = Counter()
    as_fired = Counter()
    as_withheld = Counter()

    for i, c in enumerate(as_sample, 1):
        head = (c.heading or c.text[:60]).strip()[:64]
        try:
            out = as_ex.extract(doc_type, c.text)
        except Exception as e:
            n_failed += 1
            print(f"\n   [{i}] {head}")
            print(f"       EXTRACTION FAILED: {type(e).__name__}: {str(e)[:150]}")
            continue

        as_p = out.get("params") or {}
        as_cats[out.get("category")] += 1
        for k in as_p:
            as_params_seen[k] += 1

        as_findings = as_engine.evaluate(doc_type, as_p)
        for f in as_findings:
            as_fired[f.rule_id] += 1
            if f.source and not as_index.cite(f.source,
                                             anchors=f.anchors):
                as_withheld[f.rule_id] += 1

        if verbose or as_findings or not as_p:
            print(f"\n   [{i}] {head}")
            print(f"       category:   {out.get('category')}  "
                  f"(confidence {out.get('confidence', 0):.2f})")
            print(f"       params:     {json.dumps(as_p) if as_p else 'NONE EXTRACTED'}")
            print(f"       rules fired: {[f.rule_id for f in as_findings] or 'none'}")

    print("\n" + "=" * 72)
    print("SUMMARY")
    print("=" * 72)
    print(f"   extraction failures: {n_failed} of {len(as_sample)}")
    print(f"   distinct parameters ever extracted: {len(as_params_seen)}")
    if as_params_seen:
        print(f"   most common: {dict(as_params_seen.most_common(6))}")
    print(f"   categories assigned: {dict(as_cats)}")
    print(f"   rules fired: {dict(as_fired) or 'none'}")
    if as_withheld:
        print(f"   findings withheld for want of a citation: {dict(as_withheld)}")

    print("\nREADING THIS")
    if n_failed == len(as_sample):
        print("   Every extraction call failed. The provider is reachable but not")
        print("   returning usable output. Check the error text above.")
        print("   If it mentions a rate limit, slow down:  export LLM_RPM=6")
    elif not as_params_seen:
        print("   Extraction ran but produced no parameters on any clause.")
        print("   Two very different explanations, and they are worth separating:")
        print("     a) this document genuinely has none of the clause types the rules")
        print("        cover. A bonus waiver letter has no non-compete to find.")
        print("     b) the model is answering but not with the fields the rules read.")
        print(f"   To tell them apart, scan the whole corpus:")
        print(f"       python tools/scan.py {doc_type}")
        print("   If other documents produce findings, this one was simply quiet.")
    elif not as_fired:
        print("   Parameters were extracted but no rule matched them. Either these")
        print("   clauses are genuinely clean, or the extracted values do not line up")
        print("   with what the rules test.")
        print(f"   Check whether other documents fare better: python tools/scan.py {doc_type}")
    elif as_withheld:
        print("   Rules fired but findings were withheld because their citation could")
        print("   not be resolved. Run: rm data/corpus_index.json && "
              "python tools/fetch_corpus.py")
    else:
        print("   Working as intended.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("doc_type")
    ap.add_argument("--limit", type=int, default=6)
    ap.add_argument("--verbose", action="store_true",
                    help="print every clause, not just the interesting ones")
    a = ap.parse_args()
    walk(a.path, a.doc_type, a.limit, a.verbose)
