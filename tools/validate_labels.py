"""
Run this at the end of every labeling session.

    python tools/validate_labels.py

Catches schema problems, duplicate labels, and the two things that quietly ruin
an eval set: a category nobody ever used, and a risky/not-risky split so lopsided
the Phase 4 router cannot learn anything.
"""

from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from schema import Label, read_jsonl        # noqa: E402
from typeregistry import TypeRegistry       # noqa: E402

LABEL_DIR = ROOT / "data" / "labels"
CLAUSE_DIR = ROOT / "data" / "clauses"

TARGET_PER_TYPE = 150
MIN_RISKY_SHARE = 0.20
MAX_RISKY_SHARE = 0.60


def known_clause_ids(doc_type):
    """Every clause id currently on disk for this type."""
    as_ids = set()
    for p in CLAUSE_DIR.glob("*.jsonl"):
        for r in read_jsonl(p):
            if r.get("doc_type") == doc_type:
                as_ids.add(r["clause_id"])
    return as_ids


def check(doc_type, as_profile):
    a_path = LABEL_DIR / f"{doc_type}.jsonl"
    if not a_path.exists():
        print(f"\n{doc_type}: no labels yet")
        return

    rows = read_jsonl(a_path)
    print(f"\n{doc_type}: {len(rows)} labels")

    problems = []
    seen = set()

    for r in rows:
        as_label = Label.from_dict(r)
        for p in as_label.validate(as_profile):
            problems.append(f"  {as_label.clause_id[:28]}: {p}")
        if as_label.clause_id in seen:
            problems.append(f"  {as_label.clause_id[:28]}: duplicate label")
        seen.add(as_label.clause_id)

    # labels pointing at clauses that no longer exist, which happens if the
    # segmenter changed after labeling. Catch it here rather than at eval time.
    as_known = known_clause_ids(doc_type)
    orphans = [r["clause_id"] for r in rows if as_known and r["clause_id"] not in as_known]

    # distribution
    by_cat = Counter(r["category"] for r in rows)
    by_sev = Counter(r["severity"] for r in rows)
    n_risky = sum(1 for r in rows if r["risky"])
    share = n_risky / len(rows) if rows else 0

    print(f"  risky: {n_risky} ({share:.0%})   severity: {dict(by_sev)}")

    as_blind = [r for r in rows if not r.get("assisted")]
    as_assisted = [r for r in rows if r.get("assisted")]
    if as_assisted:
        n_fixed = sum(1 for r in as_assisted if r.get("corrected"))
        print(f"  blind (reportable): {len(as_blind)}   assisted: {len(as_assisted)}, "
              f"corrected {n_fixed} ({n_fixed/len(as_assisted):.0%})")
        if len(as_blind) < 40:
            print(f"  [!] only {len(as_blind)} blind labels. Aim for 40+, they are the eval set")

    all_cats = [c["id"] for c in as_profile["taxonomy"]]
    unused = [c for c in all_cats if by_cat[c] == 0]
    thin = [c for c in all_cats if 0 < by_cat[c] < 3]

    print("  per category:")
    for c in all_cats:
        bar = "#" * min(by_cat[c], 30)
        print(f"    {c:<32} {by_cat[c]:>3}  {bar}")

    # warnings that actually matter
    if len(rows) < TARGET_PER_TYPE:
        print(f"  [ ] {TARGET_PER_TYPE - len(rows)} more to hit the {TARGET_PER_TYPE} target")
    if unused:
        print(f"  [!] never used: {unused}")
        print("      either find examples or drop them from the taxonomy before Phase 2")
    if thin:
        print(f"  [!] fewer than 3 examples: {thin}")
    if rows and not (MIN_RISKY_SHARE <= share <= MAX_RISKY_SHARE):
        print(f"  [!] risky share {share:.0%} is outside {MIN_RISKY_SHARE:.0%}-{MAX_RISKY_SHARE:.0%}")
        print("      the Phase 4 router needs a workable balance, pull in more of the rarer class")

    if orphans:
        print(f"  [!] {len(orphans)} labels point at clauses that no longer exist")
        print("      the segmenter changed after these were written. Re-label them,")
        print("      or restore the earlier data/clauses files")

    if problems:
        print(f"  [!] {len(problems)} schema problems:")
        for p in problems[:20]:
            print(p)
    else:
        print("  [ok] no schema problems")


if __name__ == "__main__":
    as_reg = TypeRegistry()
    for slug in as_reg.slugs():
        check(slug, as_reg.get(slug))
    print()
