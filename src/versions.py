"""
Cross-draft comparison.

    python src/versions.py save offer_letter v1 data/raw/employment_01.txt employment
    python src/versions.py save offer_letter v2 data/raw/employment_02.txt employment
    python src/versions.py diff offer_letter v1 v2

The question this answers is the one people actually ask after sending a redline
back: did they fix it, or did they reword it so it looks fixed?

Clauses are matched across drafts in two passes. Identical text matches on a hash.
Everything left over is matched on token overlap, which is what catches a clause
that was rewritten rather than removed. Then the findings on each matched pair are
compared, and the interesting case gets its own label:

    RESOLVED    the finding is gone and the clause changed
    VANISHED    the finding is gone but the clause did not change, so look again
    PERSISTED   same finding, same wording. They ignored it
    REWORDED    same finding, different wording. They edited around it
    NEW         a finding that was not in the earlier draft

REWORDED is the one worth building this for. A clause that changed while its
finding survived is the clearest signal that an edit was cosmetic.
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

VERSION_DIR = ROOT / "data" / "versions"
# Legal rewrites change more words than they look like they do. A clause rewritten
# from "all inventions conceived during the term" to "any invention, discovery or
# improvement conceived by the Employee" shares only about 43 percent of its tokens,
# so a 0.55 bar reported it as one removal plus one addition and hid exactly the
# case this feature exists to catch. Headings survive rewrites far better than bodies
# do, so they get their own pass first and the token bar sits lower behind it.
MATCH_THRESHOLD = 0.40
HEADING_THRESHOLD = 0.70


def _maybe_extractor():
    """Use a model if one is configured, otherwise run rules on supplied params only."""
    try:
        from extract import Extractor
        return Extractor()
    except Exception:
        return None


def norm(as_text):
    return re.sub(r"\s+", " ", as_text.lower()).strip()


def digest(as_text):
    return hashlib.sha1(norm(as_text).encode("utf-8")).hexdigest()


def tokens(as_text):
    return set(re.findall(r"[a-z0-9]{3,}", as_text.lower()))


def overlap(a, b):
    """Jaccard over token sets, which survives reordering and small rewrites."""
    as_a, as_b = tokens(a), tokens(b)
    if not as_a or not as_b:
        return 0.0
    return len(as_a & as_b) / len(as_a | as_b)


def heading_key(as_heading):
    """
    A comparable form of a clause heading.

    Numbering shifts when clauses are inserted, so the number is dropped and the
    topic words are kept. "12. INVENTIONS" and "13. INVENTIONS" both reduce to
    "inventions".
    """
    if not as_heading:
        return ""
    a_h = re.sub(r"^[\s]*(?:section|clause|article)?[\s]*"
                 r"[0-9IVXLC]+(?:\.[0-9]+)*[.):]?\s*", "", as_heading.strip(),
                 flags=re.IGNORECASE)
    a_h = re.sub(r"[^a-z ]", " ", a_h.lower())
    as_words = [w for w in a_h.split() if len(w) > 2]

    # Short headings are still headings. Filtering words under three characters threw
    # away "IP" entirely, so a rewritten intellectual property clause had no heading
    # to match on and was reported as one removal plus one addition rather than as a
    # rewrite, which is the single case this whole feature exists to catch.
    if not as_words:
        as_words = [w for w in a_h.split() if len(w) >= 2]
    return " ".join(as_words)


# ---------- storage ----------

def save(doc_id, label, as_results, doc_type):
    a_dir = VERSION_DIR / doc_id
    a_dir.mkdir(parents=True, exist_ok=True)

    as_payload = {
        "doc_id": doc_id,
        "version": label,
        "doc_type": doc_type,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "clauses": [
            {
                "text": r.get("clause_text") or r.get("text", ""),
                "heading": r.get("heading"),
                "hash": digest(r.get("clause_text") or r.get("text", "")),
                "findings": [
                    {k: f.get(k) for k in
                     ("rule_id", "category", "basis", "severity", "message", "verified")}
                    for f in (r.get("findings") or [])
                ],
            }
            for r in as_results
        ],
    }

    a_path = a_dir / f"{label}.json"
    a_path.write_text(json.dumps(as_payload, indent=2), encoding="utf-8")
    return a_path


def load(doc_id, label):
    a_path = VERSION_DIR / doc_id / f"{label}.json"
    if not a_path.exists():
        as_have = sorted(p.stem for p in (VERSION_DIR / doc_id).glob("*.json")) \
            if (VERSION_DIR / doc_id).exists() else []
        raise SystemExit(f"no version '{label}' for {doc_id}. saved: {as_have or 'none'}")
    return json.loads(a_path.read_text())


def versions(doc_id):
    a_dir = VERSION_DIR / doc_id
    return sorted(p.stem for p in a_dir.glob("*.json")) if a_dir.exists() else []


# ---------- matching ----------

def match_clauses(as_old, as_new):
    """
    Pair clauses across two drafts.

    Exact hashes first, because an untouched clause should never be reported as
    rewritten. Whatever is left is matched greedily on the best token overlap above
    the threshold, so a clause that was rewritten still lines up with its ancestor
    instead of showing as one removal and one addition.
    """
    as_pairs, as_used_new = [], set()

    by_hash = {}
    for j, c in enumerate(as_new):
        by_hash.setdefault(c["hash"], []).append(j)

    as_unmatched_old = []
    for i, c in enumerate(as_old):
        as_slots = by_hash.get(c["hash"], [])
        j = next((x for x in as_slots if x not in as_used_new), None)
        if j is None:
            as_unmatched_old.append(i)
        else:
            as_used_new.add(j)
            as_pairs.append((i, j, 1.0))

    # Second pass: headings. A rewritten clause almost always keeps its topic, so
    # this catches rewrites that the body comparison alone is too blunt for.
    as_still_open = []
    for i in as_unmatched_old:
        a_key = heading_key(as_old[i].get("heading"))
        if not a_key:
            as_still_open.append(i)
            continue

        # Exact key equality first. Token overlap alone could not see it, because
        # the tokenizer ignores words under three characters and a heading like
        # "12. IP" reduces to a single two letter word. That silently defeated the
        # heading pass for exactly the clause type it mattered most for.
        a_best, a_score = None, 0.0
        for j, c in enumerate(as_new):
            if j in as_used_new:
                continue
            if heading_key(c.get("heading")) == a_key:
                a_best, a_score = j, 1.0
                break
            s = overlap(a_key, heading_key(c.get("heading")))
            if s > a_score:
                a_best, a_score = j, s

        if a_best is not None and a_score >= HEADING_THRESHOLD:
            as_used_new.add(a_best)
            as_pairs.append((i, a_best,
                             round(overlap(as_old[i]["text"],
                                           as_new[a_best]["text"]), 3)))
        else:
            as_still_open.append(i)

    # Third pass: body token overlap, for clauses with no usable heading.
    for i in as_still_open:
        a_best, a_score = None, 0.0
        for j, c in enumerate(as_new):
            if j in as_used_new:
                continue
            s = overlap(as_old[i]["text"], c["text"])
            if s > a_score:
                a_best, a_score = j, s
        if a_best is not None and a_score >= MATCH_THRESHOLD:
            as_used_new.add(a_best)
            as_pairs.append((i, a_best, round(a_score, 3)))
        else:
            as_pairs.append((i, None, 0.0))

    for j in range(len(as_new)):
        if j not in as_used_new:
            as_pairs.append((None, j, 0.0))

    return as_pairs


# ---------- diffing ----------

def diff(doc_id, v_old, v_new):
    as_old = load(doc_id, v_old)
    as_new = load(doc_id, v_new)
    as_pairs = match_clauses(as_old["clauses"], as_new["clauses"])

    as_changes = []
    for i, j, score in as_pairs:
        a_o = as_old["clauses"][i] if i is not None else None
        a_n = as_new["clauses"][j] if j is not None else None

        as_before = {f["rule_id"]: f for f in (a_o["findings"] if a_o else [])}
        as_after = {f["rule_id"]: f for f in (a_n["findings"] if a_n else [])}
        changed_text = bool(a_o and a_n and a_o["hash"] != a_n["hash"])

        for rid, f in as_before.items():
            if rid in as_after:
                as_changes.append({
                    "status": "REWORDED" if changed_text else "PERSISTED",
                    "rule_id": rid, "severity": f["severity"], "message": f["message"],
                    "similarity": score if changed_text else 1.0,
                    "heading": (a_n or a_o).get("heading"),
                })
            elif a_n is None:
                as_changes.append({
                    "status": "RESOLVED", "rule_id": rid, "severity": f["severity"],
                    "message": f["message"], "note": "clause removed entirely",
                    "heading": a_o.get("heading"),
                })
            else:
                as_changes.append({
                    "status": "RESOLVED" if changed_text else "VANISHED",
                    "rule_id": rid, "severity": f["severity"], "message": f["message"],
                    "note": None if changed_text else
                    "the finding is gone but the wording did not change, which usually "
                    "means something else in the document moved",
                    "heading": a_n.get("heading"),
                })

        for rid, f in as_after.items():
            if rid not in as_before:
                as_changes.append({
                    "status": "NEW", "rule_id": rid, "severity": f["severity"],
                    "message": f["message"], "heading": a_n.get("heading"),
                })

    a_order = {"NEW": 0, "REWORDED": 1, "PERSISTED": 2, "VANISHED": 3, "RESOLVED": 4}
    a_sev = {"high": 0, "medium": 1, "low": 2}
    as_changes.sort(key=lambda c: (a_order.get(c["status"], 9),
                                   a_sev.get(c["severity"], 9)))

    return {
        "doc_id": doc_id, "from": v_old, "to": v_new,
        "doc_type": as_old.get("doc_type"),
        "clauses_before": len(as_old["clauses"]),
        "clauses_after": len(as_new["clauses"]),
        "summary": {k: sum(1 for c in as_changes if c["status"] == k)
                    for k in ("NEW", "REWORDED", "PERSISTED", "VANISHED", "RESOLVED")},
        "changes": as_changes,
    }


BLURB = {
    "NEW": "not in the earlier draft",
    "REWORDED": "clause was edited, the problem survived",
    "PERSISTED": "untouched",
    "VANISHED": "finding gone, wording unchanged, worth a second look",
    "RESOLVED": "fixed",
}


def render(as_diff):
    s = as_diff["summary"]
    print(f"\n{'=' * 72}")
    print(f"{as_diff['doc_id']}  {as_diff['from']} -> {as_diff['to']}")
    print(f"{as_diff['clauses_before']} clauses before, {as_diff['clauses_after']} after")
    print("=" * 72)
    print(f"  resolved {s['RESOLVED']}   reworded but unfixed {s['REWORDED']}   "
          f"untouched {s['PERSISTED']}   new {s['NEW']}")

    if s["REWORDED"]:
        print(f"\n  {s['REWORDED']} clause(s) were edited without fixing the problem.")

    for st in ("NEW", "REWORDED", "PERSISTED", "VANISHED", "RESOLVED"):
        as_rows = [c for c in as_diff["changes"] if c["status"] == st]
        if not as_rows:
            continue
        print(f"\n{st}  ({BLURB[st]})")
        for c in as_rows:
            print(f"  [{c['severity']:<6}] {c['rule_id']}  {c['message'][:88]}")
            if c.get("note"):
                print(f"            {c['note']}")
            if st == "REWORDED":
                print(f"            wording {c['similarity']:.0%} similar to the earlier draft")

    print(f"\n{'=' * 72}\nFlags for review, not legal advice.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    a_save = sub.add_parser("save", help="audit a draft and store the result")
    a_save.add_argument("doc_id")
    a_save.add_argument("version")
    a_save.add_argument("path")
    a_save.add_argument("doc_type")

    a_diff = sub.add_parser("diff", help="compare two stored drafts")
    a_diff.add_argument("doc_id")
    a_diff.add_argument("from_version")
    a_diff.add_argument("to_version")
    a_diff.add_argument("--json", help="write the raw diff here")

    a_list = sub.add_parser("list", help="stored versions for a document")
    a_list.add_argument("doc_id")

    a = ap.parse_args()

    if a.cmd == "list":
        for v in versions(a.doc_id):
            print(" ", v)

    elif a.cmd == "save":
        import segment
        from agent import ClauseAgent

        as_agent = ClauseAgent(extractor=_maybe_extractor())
        as_clauses = segment.run(a.path, a.doc_type, quiet=True)
        as_results = []
        for i, c in enumerate(as_clauses, 1):
            out = as_agent.run(a.doc_type, c.text, clause_id=c.clause_id)
            out["clause_text"] = c.text
            out["heading"] = c.heading
            as_results.append(out)
            print(f"  [{i}/{len(as_clauses)}] {len(out.get('findings') or [])} findings")
        a_path = save(a.doc_id, a.version, as_results, a.doc_type)
        print(f"\nsaved -> {a_path.relative_to(ROOT)}")

    elif a.cmd == "diff":
        d = diff(a.doc_id, a.from_version, a.to_version)
        render(d)
        if a.json:
            Path(a.json).write_text(json.dumps(d, indent=2), encoding="utf-8")
            print(f"\nraw diff -> {a.json}")
