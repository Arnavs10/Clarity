"""
Run before every push. Fails if anything git would publish looks private.

    python tools/prepush_check.py

It checks exactly the files `git add -A` would stage, plus anything already
committed, in four ways:

  1. paths that only ever hold private text: audit copies, stored drafts,
     reconstructed real letters, .env, source PDFs and DOCX
  2. clause rows from an audited upload that reached the labelling queue, the
     labels, the suggestions or the router's training files
  3. words listed in .prepush-deny, searched as raw bytes in every file. That
     includes data/router.joblib, which keeps its vocabulary as plain text, so a
     router trained after a real letter was audited carries words from it
  4. files GitHub will refuse or warn about

.prepush-deny is gitignored on purpose. One term per line, # for comments. Put in
anything that identifies a document you must not publish: the employer's name,
a manager's name, the stipend figure, a line of the address.

Exit code 0 means clean, 1 means do not push.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
A_DENY_FILE = ROOT / ".prepush-deny"

# What the corpus pipeline and the shipped fixtures leave behind. Anything else
# in data/clauses came from somewhere else, usually an audit.
AS_DOC_OK = re.compile(
    r"^((employment|rental|loan)_[\w.\-]+|(clean|risky)_(offer|rent|loan)|sample_[\w\-]+)$")

AS_PRIVATE = [
    (re.compile(r"^tests/real/"), "reconstructed text of real letters"),
    (re.compile(r"^data/versions/"), "drafts stored by /versions/save"),
    (re.compile(r"(^|/)\.env$"), "API keys"),
    (re.compile(r"(^|/)\.prepush-deny$"), "the deny list itself"),
    (re.compile(r"\.(pdf|docx)$", re.I), "a source document"),
]

AS_ROW_DIRS = ["data/clauses", "data/queue", "data/labels", "data/suggestions"]

A_WARN_BYTES = 50 * 1024 * 1024
A_MAX_BYTES = 100 * 1024 * 1024


def git(*as_args):
    a_res = subprocess.run(["git", *as_args], cwd=ROOT, capture_output=True)
    if a_res.returncode != 0:
        return None
    return a_res.stdout


def publishable():
    """Tracked files plus untracked ones that .gitignore does not exclude."""
    a_out = git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if a_out is None:
        return None
    return sorted({p for p in a_out.decode().split("\0") if p})


def ever_committed():
    a_out = git("log", "--all", "--name-only", "--format=")
    if not a_out:
        return []
    return sorted({p for p in a_out.decode().splitlines() if p.strip()})


def read_deny():
    if not A_DENY_FILE.exists():
        return None
    as_terms = []
    for a_line in A_DENY_FILE.read_text(encoding="utf-8").splitlines():
        a_line = a_line.strip()
        if a_line and not a_line.startswith("#"):
            as_terms.append(a_line)
    return as_terms


def doc_of(a_row):
    a_doc = a_row.get("doc_id")
    if not a_doc and a_row.get("clause_id"):
        a_doc = str(a_row["clause_id"]).split("::")[0]
    return a_doc


def main():
    as_files = publishable()
    if as_files is None:
        print("not a git repository yet. Run: git init")
        return 2

    as_fail, as_warn = [], []

    # 1. private paths, now and in history
    for a_rel in as_files:
        for a_pat, a_why in AS_PRIVATE:
            if a_pat.search(a_rel):
                as_fail.append(f"{a_rel}  ({a_why})")
        if a_rel.startswith("data/clauses/") and a_rel.endswith(".jsonl"):
            if not AS_DOC_OK.match(Path(a_rel).stem):
                as_fail.append(f"{a_rel}  (not a corpus or fixture file, most likely a saved audit)")

    for a_rel in ever_committed():
        for a_pat, a_why in AS_PRIVATE:
            if a_pat.search(a_rel):
                as_fail.append(f"{a_rel}  (in git HISTORY: {a_why}. Deleting it now does "
                               f"not unpublish it. Start again with a fresh git init)")

    # 2. audit rows that reached the queue, labels or training files
    for a_rel in as_files:
        if not a_rel.endswith(".jsonl"):
            continue
        if not any(a_rel.startswith(d + "/") for d in AS_ROW_DIRS):
            continue
        as_bad = set()
        for a_line in (ROOT / a_rel).read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                a_doc = doc_of(json.loads(a_line))
            except (ValueError, AttributeError):
                continue
            if a_doc and not AS_DOC_OK.match(a_doc):
                as_bad.add(a_doc)
        if as_bad:
            as_fail.append(f"{a_rel}  (rows from non-corpus documents: {sorted(as_bad)[:5]})")

    # 3. deny-listed words, as raw bytes so binaries are covered too
    as_terms = read_deny()
    if as_terms is None:
        as_fail.append(".prepush-deny is missing, so the content check could not run. "
                       "Create it with the names that must not be published")
    elif not as_terms:
        as_fail.append(".prepush-deny is empty, so the content check found nothing to look for")
    else:
        as_needles = [(t, t.lower().encode("utf-8")) for t in as_terms]
        for a_rel in as_files:
            a_path = ROOT / a_rel
            if not a_path.is_file():
                continue
            a_blob = a_path.read_bytes().lower()
            as_hit = [t for t, n in as_needles if n in a_blob]
            if as_hit:
                as_fail.append(f"{a_rel}  (contains: {', '.join(as_hit)})")

        if ever_committed():
            for a_term, _ in as_needles:
                a_out = git("log", "--all", "-i", "-S", a_term, "--format=%h")
                if a_out and a_out.strip():
                    as_fail.append(f"'{a_term}' appears in git HISTORY. Start again with a "
                                   f"fresh git init")

    # 4. sizes GitHub cares about
    for a_rel in as_files:
        a_path = ROOT / a_rel
        if not a_path.is_file():
            continue
        a_size = a_path.stat().st_size
        if a_size > A_MAX_BYTES:
            as_fail.append(f"{a_rel}  ({a_size // 2**20} MB, GitHub rejects files over 100 MB)")
        elif a_size > A_WARN_BYTES:
            as_warn.append(f"{a_rel}  ({a_size // 2**20} MB, GitHub warns over 50 MB)")

    print(f"checked {len(as_files)} files that would be published\n")
    for a_w in as_warn:
        print(f"  WARN  {a_w}")
    for a_f in as_fail:
        print(f"  FAIL  {a_f}")

    if as_fail:
        print(f"\n{len(as_fail)} problem(s). Do not push.")
        return 1
    print("clean. Nothing private found in what would be published.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
