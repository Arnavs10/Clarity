"""
The audit pipeline.

    python src/audit.py data/raw/employment_01.txt employment
    python src/audit.py --demo employment          # no API key needed

Per clause:

    segment -> extract parameters -> evaluate rules -> attach statute citation

Nothing decides risk except the rule engine, and no finding leaves without either a
verbatim statutory span or an explicit note that none was found. The agent loop and
the verification node land on top of this in Phase 3; this is the layer they call.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rules import RuleEngine        # noqa: E402
from retrieve import StatuteIndex   # noqa: E402
import segment                      # noqa: E402

BASIS_VOICE = {
    "statutory": "The Act says",
    "regulatory": "The regulator directs",
    "policy": "Below the national policy benchmark",
    "norm": "Unusual against your corpus",
}


class Auditor:
    def __init__(self, use_llm=True):
        self.engine = RuleEngine()
        self.index = StatuteIndex()
        if not self.index.chunks:
            self.index.build()
        self.extractor = None
        if use_llm:
            from extract import Extractor
            self.extractor = Extractor()

    def audit_clause(self, doc_type, clause_text, as_params=None, category=None):
        """One clause in, findings out. Pass as_params to skip the LLM."""
        as_meta = {"category": category, "confidence": 1.0, "quote": None}

        if as_params is None:
            if not self.extractor:
                raise RuntimeError("no extractor. Pass as_params, or construct with use_llm=True")
            out = self.extractor.extract(doc_type, clause_text)
            as_params = out["params"]
            as_meta = {"category": out.get("category"),
                       "confidence": out.get("confidence", 0),
                       "quote": out.get("quote"),
                       "quote_verified": out.get("quote_verified")}

        as_findings = []
        for f in self.engine.evaluate(doc_type, as_params):
            # Anchors go in so retrieval widens to a span that actually
            # contains them. Every path that resolves a citation has to
            # pass them, or this one disagrees with the agent about what
            # the law says.
            hit = (self.index.cite(f.source, anchors=f.anchors)
                   if f.source else None)
            if hit:
                f.citation_source = hit["corpus_id"]
                f.citation_span = hit["text"]
            as_findings.append(f)

        return {"category": as_meta.get("category"),
                "confidence": as_meta.get("confidence"),
                "quote": as_meta.get("quote"),
                "params": as_params,
                "findings": [f.to_dict() for f in as_findings]}

    def audit_document(self, a_path, doc_type, limit=None):
        as_clauses = segment.run(a_path, doc_type, quiet=True)
        if limit:
            as_clauses = as_clauses[:limit]

        out = []
        for i, c in enumerate(as_clauses, 1):
            try:
                res = self.audit_clause(doc_type, c.text)
            except Exception as e:
                print(f"  clause {i} failed: {e}", file=sys.stderr)
                continue
            res.update({"clause_id": c.clause_id, "order": c.order,
                        "heading": c.heading, "text": c.text})
            out.append(res)
            n = len(res["findings"])
            print(f"  [{i}/{len(as_clauses)}] {res['category']:<28} "
                  f"{n} finding{'' if n == 1 else 's'}")
        return out


def render(as_results):
    """Human-readable report. The UI in Phase 6 renders the same structure."""
    as_flagged = [r for r in as_results if r["findings"]]
    print(f"\n{'=' * 72}")
    print(f"{len(as_results)} clauses audited, {len(as_flagged)} flagged")
    print("=" * 72)

    for r in as_flagged:
        head = (r.get("heading") or r["text"][:70]).strip()
        print(f"\n{head}")
        for f in r["findings"]:
            mark = {"high": "[HIGH]", "medium": "[MED ]", "low": "[LOW ]"}.get(f["severity"], "[    ]")
            print(f"  {mark} {f['message']}")
            print(f"         basis: {f['basis']} ({f['rule_id']}) - {BASIS_VOICE[f['basis']]}")
            if f["citation_span"]:
                print(f"         cited: {f['citation_span'][:200]}...")
            elif f["basis"] in ("statutory", "regulatory", "policy"):
                print("         cited: NO SPAN FOUND, finding withheld from the report")
            if f["note"]:
                print(f"         note:  {f['note']}")

    print(f"\n{'=' * 72}")
    print("Flags for review, not legal advice.")


DEMO = {
    "employment": [
        ("9. NON-COMPETITION. For a period of twenty-four (24) months following "
         "termination of employment for any reason, the Employee shall not directly "
         "or indirectly engage in any Competitive Business within the Territory.",
         {"post_termination_restraint": True}),
        ("12. INVENTIONS. All inventions which the Employee conceives during the term, "
         "whether during or outside the usual hours of work and whether on or off the "
         "premises of the Corporation, shall be the sole and exclusive property of the "
         "Corporation.",
         {"assigns_ip": True, "covers_outside_work_hours": True,
          "covers_off_premises": True, "prior_work_carveout": False}),
        ("4. SERVICE COMMITMENT. The Employee shall serve for a minimum of three years "
         "and shall pay the Company a sum of Rs. 5,00,000 upon earlier departure.",
         {"bond_present": True, "penalty_amount_stated": True}),
        ("2. TERM. This Agreement shall be for a period of twenty-four (24) months "
         "commencing January 1, 2026.",
         {}),
    ],
    "loan": [
        ("7.2 DEFAULT INTEREST. In the event of any delay in payment, the Borrower "
         "shall pay penal interest at the rate of 24% per annum, which shall be added "
         "to the applicable rate of interest and compounded monthly.",
         {"penal_interest_added_to_rate": True, "penal_charges_capitalised": True}),
        ("9.1 ACCELERATION. Upon the occurrence of any Event of Default the entire "
         "outstanding shall become immediately due and payable without any notice or "
         "demand whatsoever.",
         {"acceleration_without_notice": True}),
        ("4.4 SET-OFF. The Lender may at any time set off any sum due hereunder "
         "against any credit balance in any account of the Borrower with the Lender.",
         {"set_off_across_unrelated_accounts": True}),
        ("2. DEFINITIONS. Capitalized terms used herein shall have the meanings set "
         "out in Schedule I to this Agreement.",
         {}),
    ],
    "rental": [
        ("3. SECURITY DEPOSIT. The Lessee shall deposit a sum equal to six (6) months "
         "rent, refundable subject to deductions at the sole discretion of the Lessor.",
         {"deposit_months": 6, "premises_type": "residential",
          "forfeiture_at_sole_discretion": True}),
        ("1. TERM. The premises are let for a period of twenty-four (24) months. This "
         "agreement is not registered.",
         {"term_months": 24, "registered": False}),
        ("5. NOTICE. The Lessor may terminate on fifteen (15) days notice. The Lessee "
         "shall give three (3) months notice.",
         {"notice_asymmetric": True, "notice_days_tenant": 90, "notice_days_landlord": 15}),
    ],
}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?")
    ap.add_argument("doc_type")
    ap.add_argument("--demo", action="store_true", help="run fixed clauses, no API key")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--json", help="write raw results here")
    a_args = ap.parse_args()

    as_auditor = Auditor(use_llm=not a_args.demo)

    if a_args.demo:
        as_results = []
        for text, as_params in DEMO[a_args.doc_type]:
            r = as_auditor.audit_clause(a_args.doc_type, text, as_params=as_params)
            r.update({"clause_id": "demo", "order": 0, "heading": text[:70], "text": text})
            as_results.append(r)
    else:
        if not a_args.path:
            print("give a file path, or use --demo")
            sys.exit(1)
        as_results = as_auditor.audit_document(a_args.path, a_args.doc_type, a_args.limit)

    render(as_results)

    if a_args.json:
        Path(a_args.json).write_text(json.dumps(as_results, indent=2), encoding="utf-8")
        print(f"\nraw results -> {a_args.json}")
