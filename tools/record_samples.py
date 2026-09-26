"""
Record a sample audit for the public demo.

    python tools/record_samples.py rental ~/Downloads/rent_agreement.pdf
    python tools/record_samples.py loan ~/Downloads/loan_agreement.pdf

Runs the document through the local API the way the page does: upload it, group
the findings one per rule, ask for the draft from those. Both are saved under
web/samples/ and the page offers them as recorded audits, which open instantly and
spend nothing. Needs the backend running on port 8000 with a model key.

Fictional documents only. What is recorded here is published with the site.

It refuses to save when:
  - any clause went unread, because a sample has to show the tool working
  - the document did not read as the type it was submitted as
  - the draft could not be written
  - any word listed in .prepush-deny appears anywhere in the result
"""

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = ROOT / "web" / "samples"
A_DENY_FILE = ROOT / ".prepush-deny"

AS_LABEL = {"rental": "Rent agreement", "loan": "Loan agreement", "employment": "Offer letter"}
AS_ORDER = ["rental", "loan", "employment"]

# The page's own grouping, in web/index.html render(). The draft is asked for with
# exactly what the page would send, so the recorded draft is the one a visitor
# would have got. selftest_v37 checks this copy against the page.
SEV_RANK = {"high": 3, "medium": 2, "low": 1, "none": 0}


def _norm_ev(a_text):
    a_text = re.sub(r"[^a-z0-9 ]+", " ", a_text.lower())
    return re.sub(r"\s+", " ", a_text).strip()


def group_issues(a_audit):
    as_issues, as_by_rule = [], {}
    for r in a_audit["results"]:
        for f in r["findings"]:
            f = {**f, "ci": r["index"], "heading": r["heading"], "clause_text": r["text"]}
            g = as_by_rule.get(f["rule_id"])
            if g is None:
                g = as_by_rule[f["rule_id"]] = {**f, "where": [], "evidence": []}
                as_issues.append(g)
            elif SEV_RANK.get(f["severity"], 0) > SEV_RANK.get(g["severity"], 0):
                g["severity"] = f["severity"]
            for e in f.get("evidence") or []:
                if not any(_norm_ev(x) == _norm_ev(e) for x in g["evidence"]):
                    g["evidence"].append(e)
            g["where"].append({"ci": f["ci"], "heading": f["heading"]})
    return as_issues


def _call(a_url, a_body=None, a_type="application/json"):
    a_req = urllib.request.Request(a_url, data=a_body,
                                   headers={"Content-Type": a_type} if a_body else {})
    try:
        with urllib.request.urlopen(a_req, timeout=1800) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except ValueError:
            return e.code, {"detail": str(e)}


def _multipart(a_name, as_bytes):
    a_boundary = "clarity" + uuid.uuid4().hex
    a_head = (f"--{a_boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
              f"filename=\"{a_name}\"\r\nContent-Type: application/octet-stream\r\n\r\n")
    a_body = a_head.encode() + as_bytes + f"\r\n--{a_boundary}--\r\n".encode()
    return a_body, f"multipart/form-data; boundary={a_boundary}"


def _version():
    a_first = (ROOT / "CHANGELOG-v37.md").read_text(encoding="utf-8").splitlines()[0]
    return a_first.lstrip("# ").strip()


def _commit():
    a_res = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                           capture_output=True, text=True)
    return a_res.stdout.strip() if a_res.returncode == 0 else None


def refuse(a_why):
    print(f"not saved: {a_why}")
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("doc_type", choices=AS_ORDER)
    ap.add_argument("path", help="a FICTIONAL document: PDF, DOCX or TXT")
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    a_args = ap.parse_args()

    a_path = Path(a_args.path).expanduser()
    if not a_path.is_file():
        refuse(f"no file at {a_path}")

    try:
        a_code, a_types = _call(a_args.api + "/doc-types")
    except urllib.error.URLError:
        refuse(f"no backend at {a_args.api}. Start it: uvicorn src.api:app --port 8000")
    if a_types.get("public") and not a_types.get("live"):
        refuse("this backend is the public demo, where live audits are off. Record locally")

    print(f"auditing {a_path.name} as {a_args.doc_type}. This takes a few minutes.")
    a_body, a_ctype = _multipart(a_path.name, a_path.read_bytes())
    a_code, a_audit = _call(f"{a_args.api}/audit/upload?doc_type={a_args.doc_type}",
                            a_body, a_ctype)
    if a_code != 200:
        refuse(f"the audit failed ({a_code}): {a_audit.get('detail')}")
    if not a_audit.get("extraction_available", True) or a_audit.get("unread", 0):
        refuse(f"{a_audit.get('unread')} of {a_audit['clauses']} clauses went unread, "
               f"probably the model's daily allowance. Record again later")
    if a_audit.get("type_status", "ok") != "ok" or not a_audit.get("draft_allowed", True):
        refuse(f"the document did not read as {a_args.doc_type}: {a_audit.get('type_message')}")

    as_issues = group_issues(a_audit)
    if not as_issues:
        refuse("no findings, so there is nothing for the sample to show")

    print(f"{len(as_issues)} issue(s). Writing the draft.")
    a_code, a_draft = _call(a_args.api + "/draft", json.dumps(
        {"doc_type": a_args.doc_type, "findings": as_issues}).encode())
    if a_code != 200 or not a_draft.get("draft"):
        refuse(f"the draft could not be written ({a_code}): {a_draft.get('detail')}")

    a_today = date.today()
    a_record = {"audit": a_audit, "draft": a_draft}
    a_meta = {"doc_type": a_args.doc_type, "label": AS_LABEL[a_args.doc_type],
              "file": f"{a_args.doc_type}.json", "version": _version(),
              "recorded_on": f"{a_today.day} {a_today:%B %Y}", "commit": _commit()}

    if A_DENY_FILE.exists():
        a_blob = json.dumps(a_record, ensure_ascii=False).lower()
        as_hit = [t.strip() for t in A_DENY_FILE.read_text(encoding="utf-8").splitlines()
                  if t.strip() and not t.startswith("#") and t.strip().lower() in a_blob]
        if as_hit:
            refuse(f"contains words from .prepush-deny: {', '.join(as_hit)}")
    else:
        print("warning: no .prepush-deny, so the result was not checked for private words")

    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    (SAMPLE_DIR / a_meta["file"]).write_text(
        json.dumps(a_record, ensure_ascii=False, indent=1), encoding="utf-8")

    a_index = SAMPLE_DIR / "index.json"
    as_have = json.loads(a_index.read_text())["samples"] if a_index.exists() else []
    as_have = [s for s in as_have if s["doc_type"] != a_args.doc_type] + [a_meta]
    as_have.sort(key=lambda s: AS_ORDER.index(s["doc_type"]))
    a_index.write_text(json.dumps({"samples": as_have}, indent=1), encoding="utf-8")

    as_sev = [g["severity"] for g in as_issues]
    print(f"saved web/samples/{a_meta['file']}: {a_audit['clauses']} clauses, "
          f"{len(as_issues)} issues ({as_sev.count('high')} high, "
          f"{as_sev.count('medium')} medium), draft included")
    print("open the page and click the sample to check it before committing")


if __name__ == "__main__":
    main()
