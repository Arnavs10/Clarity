"""
Which of my documents actually contain risky clauses?

    python tools/scan.py employment
    python tools/scan.py employment --top 5 --report

Twenty contracts pulled from EDGAR are not twenty equally interesting contracts. Some
are full executive agreements, some are one page waiving a bonus. Auditing a random
one and getting nothing back looks like a broken tool and is actually a boring
document, which is a distinction worth being able to make before recording a demo.

This runs the pipeline over every document of a type and ranks them by what came
back. The triage router keeps the cost down: most clauses never reach the model.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import segment                       # noqa: E402
from agent import ClauseAgent        # noqa: E402

RAW_DIR = ROOT / "data" / "raw"
OUT_PATH = ROOT / "data" / "scan_results.json"

SEV_WEIGHT = {"high": 3, "medium": 2, "low": 1}


def scan_document(as_agent, a_path, doc_type, max_clauses=None, as_errors_out=None):
    as_clauses = segment.run(a_path, doc_type, quiet=True)
    if max_clauses:
        as_clauses = as_clauses[:max_clauses]

    as_findings, n_skipped, n_errors = [], 0, 0
    for c in as_clauses:
        try:
            out = as_agent.run(doc_type, c.text, clause_id=c.clause_id)
        except Exception as e:
            n_errors += 1
            if as_errors_out is not None:
                as_errors_out.append(f"{type(e).__name__}: {e}")
            continue
        if not out.get("triage_keep"):
            n_skipped += 1
        if out.get("extraction_error"):
            n_errors += 1
            if as_errors_out is not None:
                as_errors_out.append(out["extraction_error"])
        for f in out.get("findings") or []:
            as_findings.append({**f, "heading": c.heading,
                                "clause": c.text[:220]})

    a_score = sum(SEV_WEIGHT.get(f["severity"], 0) for f in as_findings)
    return {
        "doc": a_path.name,
        "clauses": len(as_clauses),
        "skipped_by_triage": n_skipped,
        "errors": n_errors,
        "findings": len(as_findings),
        "high": sum(1 for f in as_findings if f["severity"] == "high"),
        "score": a_score,
        "rules": sorted({f["rule_id"] for f in as_findings}),
        "detail": as_findings,
    }


def run(doc_type, top, max_clauses, show_report, stop_after=None):
    as_files = sorted(RAW_DIR.glob(f"{doc_type}_*.txt"))
    if not as_files:
        raise SystemExit(
            f"no documents at data/raw/{doc_type}_*.txt\n"
            f"run: python tools/fetch_documents.py --type {doc_type} --n 20")

    as_agent = ClauseAgent(extractor=_extractor())
    if as_agent.extractor is None:
        print("no model provider configured, so only norm rules can fire.")
        print("set one up in .env and re-run for the full picture.\n")

    import llm
    print(f"scanning {len(as_files)} {doc_type} documents")
    if as_agent.extractor is not None:
        a_per_min = min(llm.rpm(), max(1, llm.tpm() // 2500))
        print(f"pacing at {llm.rpm()} calls/min and {llm.tpm()} tokens/min, "
              f"so roughly {a_per_min} clauses a minute")
        if max_clauses:
            a_est = len(as_files) * max_clauses * 0.4 / a_per_min
            print(f"about {a_est:.0f} minutes for this pass "
                  f"(the router skips most clauses)\n")
        else:
            print("a full pass on a free tier takes a while. "
                  "Use --max-clauses 8 first.\n")
    else:
        print()

    as_rows, as_errors = [], []
    for i, f in enumerate(as_files, 1):
        r = scan_document(as_agent, f, doc_type, max_clauses, as_errors)
        as_rows.append(r)

        # Write after every document. A free tier pass takes long enough that
        # stopping partway through should still leave usable results behind.
        OUT_PATH.write_text(json.dumps(
            [{k: v for k, v in x.items() if k != "detail"} for x in as_rows],
            indent=2), encoding="utf-8")

        # Abort rather than grind through twenty documents producing nothing. An
        # earlier version counted errors and never printed one, so a run that was
        # entirely rate limited looked exactly like a corpus of clean contracts.
        if i >= 2 and as_errors and not any(x["findings"] for x in as_rows):
            print(f"\n  STOPPING after {i} documents.")
            print(f"  {len(as_errors)} clauses errored and nothing has been found.")
            print(f"  First error:\n    {as_errors[0][:220]}")
            print("\n  This is a backend problem, not a quiet corpus.")
            if "429" in as_errors[0] or "rate" in as_errors[0].lower():
                print("  Rate limited. Slow it down:  export LLM_RPM=6")
                print("  Or switch provider in .env; groq allows a higher rate.")
            return as_rows

        if stop_after and sum(1 for x in as_rows if x["findings"]) >= stop_after:
            print(f"\n  {stop_after} documents with findings, that is enough. Stopping.")
            break
        a_flag = "  <-- worth a look" if r["high"] else ""
        print(f"  [{i:>2}/{len(as_files)}] {f.name:<22} "
              f"{r['clauses']:>3} clauses  {r['findings']:>2} findings  "
              f"{r['high']:>2} high{a_flag}")

    as_rows.sort(key=lambda r: (-r["score"], -r["findings"]))

    print(f"\n{'=' * 72}")
    print(f"RANKED, best demo candidates first")
    print("=" * 72)
    for r in as_rows[:top]:
        if not r["findings"]:
            continue
        print(f"\n  {r['doc']}   score {r['score']}   "
              f"{r['findings']} findings ({r['high']} high)")
        print(f"    rules: {', '.join(r['rules'])}")

    n_empty = sum(1 for r in as_rows if not r["findings"])
    n_total_f = sum(r["findings"] for r in as_rows)
    n_err = sum(r["errors"] for r in as_rows)

    print(f"\n{'=' * 72}")
    print(f"  {len(as_rows)} documents, {n_total_f} findings total")
    print(f"  {len(as_rows) - n_empty} documents produced at least one finding")
    print(f"  {n_empty} produced nothing")
    if n_err:
        print(f"  {n_err} clauses errored during extraction")
        print(f"  first error: {as_errors[0][:200]}" if as_errors else "")

    as_all_rules = Counter(rl for r in as_rows for rl in r["rules"])
    if as_all_rules:
        print(f"\n  rules that ever fired: {dict(as_all_rules)}")
    a_never = [r["id"] for r in as_agent.engine.rules_for(doc_type)
               if r["id"] not in as_all_rules]
    if a_never:
        print(f"  never fired on this corpus: {a_never}")
        print("  that is information, not a bug: this corpus may simply not contain")
        print("  those clause types.")

    if n_empty == len(as_rows):
        print("\n  Nothing fired anywhere. Run tools/diagnose.py on one document.")
    elif as_rows and as_rows[0]["findings"]:
        print(f"\n  Use {as_rows[0]['doc']} for the demo. It has the most to show.")

    if show_report and as_rows and as_rows[0]["findings"]:
        print(f"\n{'=' * 72}\nDETAIL, {as_rows[0]['doc']}\n{'=' * 72}")
        for f in as_rows[0]["detail"]:
            print(f"\n  [{f['severity']}] {f['rule_id']}  ({f['basis']})")
            print(f"    {f['message']}")
            print(f"    clause: {f['clause'][:150]}...")

    OUT_PATH.write_text(json.dumps(
        [{k: v for k, v in r.items() if k != "detail"} for r in as_rows],
        indent=2), encoding="utf-8")
    print(f"\nsaved -> {OUT_PATH.relative_to(ROOT)}")
    return as_rows


def _extractor():
    try:
        from extract import Extractor
        return Extractor()
    except Exception:
        return None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("doc_type")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--max-clauses", type=int, default=None,
                    help="cap clauses per document, useful for a quick pass")
    ap.add_argument("--report", action="store_true",
                    help="print every finding from the best document")
    ap.add_argument("--stop-after", type=int, default=None,
                    help="stop once this many documents have produced findings")
    a = ap.parse_args()
    run(a.doc_type, a.top, a.max_clauses, a.report, a.stop_after)
