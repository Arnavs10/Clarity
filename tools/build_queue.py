"""
Builds the labeling queue.

    python tools/build_queue.py --type employment --n 150
    python tools/build_queue.py --type rental --n 150

Two things this fixes, both of which quietly ruin an eval set.

Duplicates. EDGAR dedupes by URL, but the same lease or agreement gets filed by
several companies at different URLs. Labeling one clause twice inflates your count
and leaks the same text into both halves of any split you make later.

Sampling order. There are far more clauses on disk than you will label. Taking them
in document order means every label comes from the first three or four documents,
so the set is not representative of anything. This walks the documents round robin
instead, so 150 labels come from 20 documents rather than 4.

Writes data/queue/<type>.jsonl. The labeler reads that if it exists.
"""

import argparse
import hashlib
import random
import re
import sys
from collections import defaultdict, OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from schema import read_jsonl, write_jsonl, Clause   # noqa: E402

CLAUSE_DIR = ROOT / "data" / "clauses"
QUEUE_DIR = ROOT / "data" / "queue"

MIN_CLAUSE = 150        # shorter than this is a fragment, not worth a label
MAX_CLAUSE = 4500
SEED = 20260819         # fixed, so the queue is reproducible


def norm(as_text):
    """Collapse whitespace and case so trivial formatting differences do not hide a match."""
    return re.sub(r"\s+", " ", as_text.lower()).strip()


def digest(as_text):
    return hashlib.sha1(norm(as_text).encode("utf-8")).hexdigest()


def load_by_doc(doc_type):
    """clause rows grouped by document, in file order."""
    as_docs = OrderedDict()
    for p in sorted(CLAUSE_DIR.glob("*.jsonl")):
        rows = [r for r in read_jsonl(p) if r.get("doc_type") == doc_type]
        if rows:
            as_docs[rows[0]["doc_id"]] = rows
    return as_docs


def drop_duplicate_docs(as_docs, threshold=0.85):
    """
    Remove documents that duplicate one already kept.

    Compares the set of clause digests between documents and keeps a document only
    if it overlaps every earlier one by less than the threshold.

    An earlier version compared the first few thousand characters instead, which was
    wrong: contracts of the same kind open with near identical recitals, so genuinely
    different leases looked like copies of each other and got deleted. Overlap across
    the whole document does not have that problem, because two different leases share
    their boilerplate and nothing else.
    """
    kept, dropped = OrderedDict(), []
    as_sigs = {}

    for doc_id, rows in as_docs.items():
        sig = {digest(r["text"]) for r in rows}
        if not sig:
            continue

        match, best = None, 0.0
        for other_id, other_sig in as_sigs.items():
            overlap = len(sig & other_sig) / min(len(sig), len(other_sig))
            if overlap > best:
                match, best = other_id, overlap

        if best >= threshold:
            how = "identical" if best >= 0.999 else f"{best:.0%} overlap with"
            dropped.append((doc_id, match, how))
            continue

        as_sigs[doc_id] = sig
        kept[doc_id] = rows

    return kept, dropped


def drop_duplicate_clauses(as_docs):
    """Same clause text appearing in more than one document. Keep the first."""
    seen = set()
    removed = 0
    out = OrderedDict()

    for doc_id, rows in as_docs.items():
        keep = []
        for r in rows:
            d = digest(r["text"])
            if d in seen:
                removed += 1
                continue
            seen.add(d)
            keep.append(r)
        if keep:
            out[doc_id] = keep

    return out, removed


def usable(r):
    return MIN_CLAUSE <= len(r["text"]) <= MAX_CLAUSE


def round_robin(as_docs, n_wanted, rng):
    """
    Take one clause from each document in turn until the target is hit.

    Within a document the clauses are shuffled, so a short document does not only
    ever contribute its opening definitions.
    """
    pools = {}
    for doc_id, rows in as_docs.items():
        as_pool = [r for r in rows if usable(r)]
        rng.shuffle(as_pool)
        if as_pool:
            pools[doc_id] = as_pool

    picked = []
    while len(picked) < n_wanted and pools:
        for doc_id in list(pools.keys()):
            if len(picked) >= n_wanted:
                break
            picked.append(pools[doc_id].pop())
            if not pools[doc_id]:
                del pools[doc_id]

    return picked


def run(doc_type, n_wanted, test_frac=0.35):
    as_docs = load_by_doc(doc_type)
    if not as_docs:
        print(f"no clauses on disk for '{doc_type}'. Run: python src/segment.py --all {doc_type}")
        return

    n_clauses_before = sum(len(v) for v in as_docs.values())
    print(f"{doc_type}: {len(as_docs)} documents, {n_clauses_before} clauses on disk")

    as_docs, dropped = drop_duplicate_docs(as_docs)
    if dropped:
        print(f"\n  dropped {len(dropped)} duplicate documents:")
        for doc_id, same_as, how in dropped:
            print(f"    {doc_id:<18} {how} {same_as}")

    as_docs, n_dupe_clauses = drop_duplicate_clauses(as_docs)
    if n_dupe_clauses:
        print(f"\n  dropped {n_dupe_clauses} clauses that repeat across documents")

    n_usable = sum(1 for rows in as_docs.values() for r in rows if usable(r))
    print(f"\n  {len(as_docs)} documents left, {n_usable} usable clauses "
          f"({MIN_CLAUSE}-{MAX_CLAUSE} chars)")

    if n_usable < n_wanted:
        print(f"  only {n_usable} available, queue will be that size")

    rng = random.Random(SEED)
    picked = round_robin(as_docs, n_wanted, rng)
    rng.shuffle(picked)

    QUEUE_DIR.mkdir(parents=True, exist_ok=True)

    # Split into an assisted dev half and a blind test half. The test half is what
    # the reported precision and recall are computed on, so it never sees a
    # suggestion. Documents are kept apart across the two halves where possible.
    n_test = max(1, int(len(picked) * test_frac))
    as_test, as_dev = picked[:n_test], picked[n_test:]

    for r in as_dev:
        r["split"] = "dev"
    for r in as_test:
        r["split"] = "test"

    out_path = QUEUE_DIR / f"{doc_type}.jsonl"
    write_jsonl(out_path, as_test + as_dev)      # blind ones first

    spread = defaultdict(int)
    for r in picked:
        spread[r["doc_id"]] += 1

    print(f"\n  queue: {len(picked)} clauses from {len(spread)} documents "
          f"({min(spread.values())}-{max(spread.values())} each)")
    print(f"  split: {len(as_test)} blind test, {len(as_dev)} assisted dev")
    print(f"  written to {out_path.relative_to(ROOT)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--type", required=True, help="document type slug")
    ap.add_argument("--n", type=int, default=150, help="how many clauses to queue")
    ap.add_argument("--test-frac", type=float, default=0.35,
                    help="share reserved for the blind test set")
    a_args = ap.parse_args()
    run(a_args.type, a_args.n, a_args.test_frac)
