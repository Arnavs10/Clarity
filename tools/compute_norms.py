"""
Computes the market-norm thresholds from the user's own contract corpus.

    python tools/compute_norms.py --type employment

Every rule with basis: norm reads its threshold from here rather than from a number
somebody chose. The output is a percentile over real filed contracts, so the finding
reads "longer than 90 percent of the contracts in this corpus" and anyone can rerun
the script and get the same number.

Needs an API key: extracting "notice is 90 days" from prose is the same extraction
job as the main pipeline. Run it once, commit data/norms.json, and the rule engine
reads the file from then on.
"""

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from schema import read_jsonl        # noqa: E402
from extract import Extractor        # noqa: E402

CLAUSE_DIR = ROOT / "data" / "clauses"
OUT_PATH = ROOT / "data" / "norms.json"

# Only numeric fields have percentiles. Boolean fields get a base rate instead,
# which is still a measured fact about the corpus.
NUMERIC = {
    "employment": ["notice_days_employee", "notice_days_employer",
                   "garden_leave_days", "probation_months", "confidentiality_years"],
    "rental": ["deposit_months", "escalation_percent", "term_months",
               "notice_days_tenant", "notice_days_landlord", "cure_period_days"],
}

MIN_SAMPLES = 8


def percentiles(as_values):
    as_values = sorted(v for v in as_values if isinstance(v, (int, float)))
    if len(as_values) < MIN_SAMPLES:
        return None
    return {
        "n": len(as_values),
        "median": round(statistics.median(as_values), 2),
        "p75": round(as_values[int(len(as_values) * 0.75)], 2),
        "p90": round(as_values[min(int(len(as_values) * 0.90), len(as_values) - 1)], 2),
        "min": as_values[0],
        "max": as_values[-1],
    }


def run(doc_type, limit):
    rows = []
    for p in sorted(CLAUSE_DIR.glob("*.jsonl")):
        rows += [r for r in read_jsonl(p) if r.get("doc_type") == doc_type]
    if limit:
        rows = rows[:limit]
    if not rows:
        print(f"no clauses for {doc_type}. Run src/segment.py first")
        sys.exit(1)

    as_ex = Extractor()
    as_values = defaultdict(list)
    as_bools = defaultdict(lambda: [0, 0])

    print(f"scanning {len(rows)} {doc_type} clauses for numeric terms\n")
    for i, r in enumerate(rows, 1):
        try:
            out = as_ex.extract(doc_type, r["text"])
        except Exception:
            continue
        for k, v in (out.get("params") or {}).items():
            if isinstance(v, bool):
                as_bools[k][int(v)] += 1
            elif isinstance(v, (int, float)):
                as_values[k].append(v)
        if i % 25 == 0:
            print(f"  {i}/{len(rows)}")

    as_norms = {}
    if OUT_PATH.exists():
        as_norms = json.loads(OUT_PATH.read_text())

    print("\nnumeric fields:")
    for f in NUMERIC.get(doc_type, []):
        p = percentiles(as_values.get(f, []))
        if p:
            as_norms[f] = p
            print(f"  {f:<26} n={p['n']:<4} median {p['median']:<7} p90 {p['p90']}")
        else:
            got = len(as_values.get(f, []))
            print(f"  {f:<26} only {got} samples, need {MIN_SAMPLES}. Rules on it stay silent")

    print("\nboolean base rates:")
    for f, (no, yes) in sorted(as_bools.items()):
        tot = no + yes
        if tot >= MIN_SAMPLES:
            as_norms[f] = {"n": tot, "true_rate": round(yes / tot, 3)}
            print(f"  {f:<34} {yes}/{tot} = {yes/tot:.0%}")

    OUT_PATH.write_text(json.dumps(as_norms, indent=2), encoding="utf-8")
    print(f"\n{len(as_norms)} metrics -> {OUT_PATH.relative_to(ROOT)}")
    print("Rules with basis: norm now have thresholds. Ones without enough samples stay silent.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--type", required=True)
    ap.add_argument("--limit", type=int, default=120)
    a = ap.parse_args()
    run(a.type, a.limit)
