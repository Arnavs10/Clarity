"""
Regression tests for the v37 corrections.

    python tools/selftest_v37.py

Every test here exists because something went wrong on a real document on
17 September 2026, when rental and loan were audited for the first time against
agreements neither the author nor the reviewer had written. Nine findings came
back across three runs. None was clean.

The failures, and the test that now pins each one:

  1  An internship offer letter was audited as a loan agreement. Nothing checked
     the document against the type, so the loan rules read it and reported that
     the entire balance could be called in with no notice, on the clause where
     the candidate confirms his degree.                      -> guard, gates, end to end

  2  A rent agreement's administrative-charges clause produced escalation_undefined
     and was reported as an uncapped rent revision. Rent is fixed elsewhere in that
     agreement and the clause never mentions it.             -> gates

  3  R-LOAN-04 said penal charges were never quantified, from the auto-debit clause,
     while clause 7 of the same agreement set the late charge at 2% per month.
     "Never" is a claim about the document.                  -> docscan, rules

  4  R-RENT-10 could not fire on an unregistered lease, because a document with no
     registration clause never produces the field the rule reads. Silence was
     invisible.                                              -> docscan, rules

  5  Lock-in and set-off were reported high while a 5% prepayment fee was medium.
     High on the norm tier rests on nothing a reader can open. -> severity cap

  6  Page footers numbered per page were never recognised as repeats, and page
     banners carrying the page number were never recognised at all, so both landed
     inside clauses and one clause was swallowed whole.      -> segmentation

The unit sections need nothing but the source tree. The end to end section needs
the statute corpus, like every other tool here, and says so rather than passing
quietly without it.
"""

import io
import contextlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import deterministic                    # noqa: E402
import docscan                          # noqa: E402
import doctype_guard as guard           # noqa: E402
import segment                          # noqa: E402
from rules import RuleEngine            # noqa: E402

AS_RESULTS = []


def check(a_section, a_name, a_got, a_want):
    a_ok = a_got == a_want
    AS_RESULTS.append((a_section, a_name, a_ok, a_got, a_want))
    return a_ok


def section(a_title):
    print(f"\n{'=' * 72}\n{a_title}\n{'=' * 72}")


# ---------------------------------------------------------------------------
# 1. the document type guard
# ---------------------------------------------------------------------------

def test_guard():
    section("1  DOCUMENT TYPE GUARD")

    as_docs = {
        "employment": ["tests/fixtures/clean_offer.txt", "tests/fixtures/risky_offer.txt"],
        "rental": ["tests/fixtures/clean_rent.txt", "tests/fixtures/risky_rent.txt"],
        "loan": ["tests/fixtures/clean_loan.txt", "tests/fixtures/risky_loan.txt"],
    }
    for a_real in Path(ROOT / "tests" / "real").glob("*_full.txt"):
        as_docs["employment"].append(str(a_real.relative_to(ROOT)))

    a_pass = a_fail = 0
    for a_true, as_paths in as_docs.items():
        for a_rel in as_paths:
            a_path = ROOT / a_rel
            if not a_path.exists():
                continue
            a_text = a_path.read_text(errors="ignore")
            for a_sel in as_docs:
                a_want = "ok" if a_sel == a_true else "mismatch"
                a_got = guard.check(a_text, a_sel)["status"]
                if a_got == a_want:
                    a_pass += 1
                else:
                    a_fail += 1
                    print(f"  WRONG  {a_path.name} true={a_true} read_as={a_sel} "
                          f"got={a_got} want={a_want}")
    print(f"  every document against every type: {a_pass} correct, {a_fail} wrong")
    check("guard", "cross product of documents and types", a_fail, 0)

    # The exact failure. An offer letter, read as a loan agreement.
    a_letter = next(iter(sorted((ROOT / "tests" / "real").glob("*_full.txt"))),
                    ROOT / "tests" / "real" / "letter_full.txt")
    if a_letter.exists():
        a_out = guard.check(a_letter.read_text(errors="ignore"), "loan")
        print(f"  offer letter read as loan: {a_out['status']}, "
              f"suggests {a_out['suggested']}")
        check("guard", "offer letter read as loan is caught", a_out["status"], "mismatch")
        check("guard", "and the right type is suggested", a_out["suggested"], "employment")

    # Whatever arrives from an upload, this must not raise.
    for a_name, a_value in [("empty", ""), ("none", None), ("null bytes", "\x00" * 400),
                            ("devanagari", "यह एक अनुबंध है। " * 90),
                            ("one sentence", "Rent shall be paid monthly."),
                            ("digits only", " ".join(str(i) for i in range(500)))]:
        try:
            a_status = guard.check(a_value, "rental")["status"]
            check("guard", f"survives {a_name}", True, True)
        except Exception as e:                                   # noqa: BLE001
            print(f"  RAISED on {a_name}: {type(e).__name__}")
            check("guard", f"survives {a_name}", False, True)
    print("  hostile inputs: none raised")


# ---------------------------------------------------------------------------
# 2. topic gates
# ---------------------------------------------------------------------------

def test_gates():
    section("2  TOPIC GATES  (a fact is only trusted about a clause on that subject)")

    # Verbatim from the documents that failed.
    A_REPRESENTATIONS = (
        "a. all information and documents furnished by the Candidate in support of "
        "his/her educational qualifications and professional experience are correct "
        "and true; b. the Candidate has never been convicted of any offence")
    A_ABSENCE = (
        "Candidate is found to have absented himself/herself for 5 (five) days without "
        "prior permission, misbehaved, committed financial irregularity")
    A_ADMIN_CHARGES = (
        "The Landlord may introduce or revise reasonable administrative, "
        "document-processing or service charges on not less than 15 days' written "
        "notice where such charges relate to services requested by the Tenant.")
    A_AUTODEBIT = (
        "The Borrower authorises the Lender to submit repayment instructions under the "
        "electronic mandate supplied. If a debit is returned unpaid, the Borrower shall "
        "reimburse any bank or network charges actually imposed.")

    print("  must be DROPPED  (the four findings that were actually wrong)")
    for a_type, a_field, a_text, a_why in [
        ("loan", "acceleration_without_notice", A_REPRESENTATIONS,
         "reported as calling in the entire balance"),
        ("loan", "assignment_without_notice", A_ABSENCE,
         "reported as the loan being sold on"),
        ("loan", "jurisdiction_lender_city_only", A_ABSENCE,
         "reported as disputes heard where the lender is"),
        ("rental", "escalation_undefined", A_ADMIN_CHARGES,
         "reported as an uncapped rent revision"),
        ("loan", "penal_charge_amount_undisclosed", A_AUTODEBIT,
         "reported as penal charges never quantified"),
    ]:
        _, as_dropped = deterministic.apply_gates(a_type, a_text, {a_field: True})
        a_ok = len(as_dropped) == 1
        print(f"    {'ok ' if a_ok else 'NO '} {a_field:<34} {a_why}")
        check("gates", f"drops {a_field} off topic", a_ok, True)

    print("  must be KEPT  (the same fields on clauses that really are about them)")
    for a_type, a_field, a_text in [
        ("loan", "acceleration_without_notice",
         "Following an Event of Default the Lender may declare all outstanding "
         "Principal immediately due and payable."),
        ("loan", "assignment_without_notice",
         "The Lender may assign or transfer its rights to a securitisation vehicle."),
        ("loan", "jurisdiction_lender_city_only",
         "Disputes shall be subject to the exclusive jurisdiction of the courts at "
         "Mumbai."),
        ("loan", "penal_charge_amount_undisclosed",
         "Penal charges shall be levied as the Lender may decide."),
        ("loan", "set_off_across_unrelated_accounts",
         "The Lender may set off matured amounts against credit balances."),
        ("rental", "escalation_undefined",
         "The rent shall be enhanced by ten percent on each anniversary."),
        ("rental", "escalation_undefined",
         "Escalation: the monthly consideration shall be revised annually."),
        ("rental", "lock_in_binds_tenant_only",
         "The Tenant shall not vacate before the minimum period of eleven months."),
        ("employment", "post_termination_restraint",
         "For twelve months following the termination the Candidate shall not compete."),
    ]:
        _, as_dropped = deterministic.apply_gates(a_type, a_text, {a_field: True})
        a_ok = not as_dropped
        print(f"    {'ok ' if a_ok else 'NO '} {a_field:<34} {a_text[:44]}...")
        check("gates", f"keeps {a_field} on topic: {a_text[:24]}", a_ok, True)

    # A gate refuses a claim. It has nothing to say about a denial.
    as_kept, as_dropped = deterministic.apply_gates(
        "loan", A_REPRESENTATIONS, {"acceleration_without_notice": False})
    check("gates", "a false value is never gated", as_dropped, [])
    # An ungated field passes through untouched.
    as_kept, _ = deterministic.apply_gates("loan", A_REPRESENTATIONS,
                                           {"some_future_field": True})
    check("gates", "an ungated field is left alone", as_kept.get("some_future_field"), True)
    print("  false values and ungated fields pass through untouched")


# ---------------------------------------------------------------------------
# 3. document level facts
# ---------------------------------------------------------------------------

def test_docscan():
    section("3  DOCUMENT LEVEL FACTS  (a claim about the agreement, read once)")

    A_REAL_LOAN = """
    1. PRINCIPAL
    The Lender shall advance Rs. 8,75,000 to the Borrower.

    7. LATE CHARGES
    For any instalment not paid by the due date, the Borrower shall pay a late charge
    of 2% per month on the overdue instalment amount.

    10. AUTO-DEBIT MANDATE
    If a debit is returned unpaid, the Borrower shall reimburse any bank or network
    charges actually imposed, subject to applicable law.
    """
    as_p, _ = docscan.read_document("loan", A_REAL_LOAN)
    print("  the loan agreement that was reported wrong:")
    print(f"    penal charge quantified somewhere: {as_p['doc__penal_charge_quantified']}"
          f"   (clause 7 says 2% per month)")
    print(f"    quantum expressly withheld:        {as_p['doc__penal_quantum_withheld']}")
    check("docscan", "real loan: penal charge seen as quantified",
          as_p["doc__penal_charge_quantified"], True)
    check("docscan", "real loan: nothing withheld",
          as_p["doc__penal_quantum_withheld"], False)

    A_WITHHELD = """
    4. PENAL CHARGES
    Penal charges shall be levied as the Lender may decide. The Lender is not required
    to disclose the quantum of such charges in advance.
    """
    as_p, _ = docscan.read_document("loan", A_WITHHELD)
    print("  an agreement that refuses to give a figure:")
    print(f"    quantum expressly withheld:        {as_p['doc__penal_quantum_withheld']}")
    check("docscan", "withheld quantum is detected",
          as_p["doc__penal_quantum_withheld"], True)

    # The window must not cross a clause boundary. A section heading reaching
    # backwards into the previous clause found the principal of the loan and
    # reported the default interest as quantified when it was not.
    A_CROSS = """
    2. PRINCIPAL
    The Lender shall advance Rs. 5,00,000 to the Borrower.

    3. DEFAULT INTEREST
    The Borrower shall pay penal charges as may be notified from time to time.
    """
    as_p, _ = docscan.read_document("loan", A_CROSS)
    print(f"  a figure in the previous clause is not this clause's quantum: "
          f"{as_p['doc__penal_charge_quantified']}")
    check("docscan", "does not read across a clause boundary",
          as_p["doc__penal_charge_quantified"], False)

    A_UNREGISTERED = """
    This Agreement is made for a period of 12 months. Rent shall be INR 31,500 per
    month. The Security Deposit is held without interest.
    """
    as_p, _ = docscan.read_document("rental", A_UNREGISTERED)
    print(f"  a lease with no registration clause: term={as_p.get('doc__term_months')} "
          f"months, registration mentioned={as_p['doc__registration_mentioned']}")
    check("docscan", "term read from the document", as_p.get("doc__term_months"), 12)
    check("docscan", "silence about registration is visible",
          as_p["doc__registration_mentioned"], False)

    as_p, _ = docscan.read_document(
        "rental", "This lease for a term of 11 months shall be registered before the "
                  "Sub-Registrar and stamp duty shall be borne equally.")
    check("docscan", "registration mentioned is seen",
          as_p["doc__registration_mentioned"], True)

    # Number words. Indian drafting writes "twenty-four percent" as often as "24%".
    as_p, _ = docscan.read_document(
        "loan", "The Borrower shall pay penal interest at twenty-four percent per annum.")
    check("docscan", "a rate written in words counts as quantified",
          as_p["doc__penal_charge_quantified"], True)

    for a_name, a_value in [("empty", ""), ("none", None), ("null bytes", "\x00" * 300),
                            ("devanagari", "अनुबंध " * 200)]:
        try:
            docscan.read_document("loan", a_value)
            docscan.read_document("rental", a_value)
            check("docscan", f"survives {a_name}", True, True)
        except Exception:                                        # noqa: BLE001
            check("docscan", f"survives {a_name}", False, True)
    print("  hostile inputs: none raised")

    # Every key the type declares is always present, or a rule written against a
    # false value silently sees None and fails closed, which is the original bug.
    as_p, _ = docscan.read_document("loan", "")
    check("docscan", "loan keys always emitted",
          sorted(as_p), ["doc__kfs_present", "doc__penal_charge_quantified",
                         "doc__penal_quantum_withheld",
                         "doc__prepayment_charge_quantified"])


# ---------------------------------------------------------------------------
# 4. the rule engine
# ---------------------------------------------------------------------------

def test_rules():
    section("4  RULE ENGINE  (severity has to be able to show its working)")

    a_engine = RuleEngine()

    a_bad = 0
    for a_type in a_engine.doc_types():
        for r in a_engine.rules_for(a_type):
            if (r["basis"] == "norm" and r.get("severity") == "high"
                    and not r.get("escalate_to_high_when")):
                print(f"    {r['id']} claims high on the norm tier without naming why")
                a_bad += 1
    print(f"  norm rules claiming high without an escalation condition: {a_bad}")
    check("rules", "no unexplained high on the norm tier", a_bad, 0)

    # The two that were demoted on evidence.
    for a_id, a_type in [("R-RENT-04", "rental"), ("R-LOAN-08", "loan")]:
        r = next(x for x in a_engine.rules_for(a_type) if x["id"] == a_id)
        print(f"  {a_id}: {r['severity']} by default, high when "
              f"{list(r['escalate_to_high_when'])[0]}")
        check("rules", f"{a_id} no longer high by default", r["severity"], "medium")

    # R-RENT-04 ordinary case stays medium, severe case still reaches high.
    as_f = a_engine.evaluate("rental", {"lock_in_binds_tenant_only": True})
    check("rules", "one sided lock-in alone is medium",
          [f.severity for f in as_f if f.rule_id == "R-RENT-04"], ["medium"])
    as_f = a_engine.evaluate("rental", {"lock_in_binds_tenant_only": True,
                                        "early_exit_forfeits_full_deposit": True})
    check("rules", "lock-in that forfeits the deposit is high",
          [f.severity for f in as_f if f.rule_id == "R-RENT-04"], ["high"])

    as_f = a_engine.evaluate("loan", {"set_off_across_unrelated_accounts": True})
    check("rules", "set-off with notice is medium",
          [f.severity for f in as_f if f.rule_id == "R-LOAN-08"], ["medium"])
    as_f = a_engine.evaluate("loan", {"set_off_across_unrelated_accounts": True,
                                      "set_off_without_notice": True})
    check("rules", "set-off without notice is high",
          [f.severity for f in as_f if f.rule_id == "R-LOAN-08"], ["high"])
    print("  demoted rules still reach high on their severe form")

    # R-LOAN-04 now needs the document to agree with it.
    as_f = a_engine.evaluate("loan", {"penal_charge_amount_undisclosed": True,
                                      "doc__penal_charge_quantified": True,
                                      "doc__penal_quantum_withheld": False})
    check("rules", "R-LOAN-04 silent when the document does quantify",
          [f.rule_id for f in as_f if f.rule_id == "R-LOAN-04"], [])
    as_f = a_engine.evaluate("loan", {"penal_charge_amount_undisclosed": True,
                                      "doc__penal_charge_quantified": False,
                                      "doc__penal_quantum_withheld": False})
    check("rules", "R-LOAN-04 fires when the document never quantifies",
          [f.rule_id for f in as_f if f.rule_id == "R-LOAN-04"], ["R-LOAN-04"])
    as_f = a_engine.evaluate("loan", {"penal_charge_amount_undisclosed": True,
                                      "doc__penal_charge_quantified": True,
                                      "doc__penal_quantum_withheld": True})
    check("rules", "R-LOAN-04 fires when the quantum is expressly withheld",
          [f.rule_id for f in as_f if f.rule_id == "R-LOAN-04"], ["R-LOAN-04"])
    print("  R-LOAN-04 now answers to the document, not to one clause")

    # R-RENT-10 can finally see an unregistered lease.
    as_f = a_engine.evaluate("rental", {"doc__term_months": 13,
                                        "doc__registration_mentioned": False})
    check("rules", "R-RENT-10 fires on an unregistered 13 month lease",
          [f.rule_id for f in as_f if f.rule_id == "R-RENT-10"], ["R-RENT-10"])
    as_f = a_engine.evaluate("rental", {"doc__term_months": 11,
                                        "doc__registration_mentioned": False})
    check("rules", "R-RENT-10 silent on an 11 month lease",
          [f.rule_id for f in as_f if f.rule_id == "R-RENT-10"], [])
    print("  R-RENT-10 can now see a registration clause that is not there")

    # A doc-scope rule must be anchored to the clause in front of it.
    a_caught = False
    try:
        import tempfile
        import textwrap
        with tempfile.TemporaryDirectory() as a_dir:
            Path(a_dir, "x.yaml").write_text(textwrap.dedent("""
                version: 1
                doc_type: rental
                rules:
                  - id: R-BAD-01
                    category: security_deposit
                    when: {doc__registration_mentioned: false}
                    basis: norm
                    metric: x
                    severity: medium
                    message: fires on every clause
            """))
            RuleEngine(rules_dir=a_dir)
    except ValueError as e:
        a_caught = "only document-scope fields" in str(e)
    check("rules", "a document-only rule is refused at load", a_caught, True)
    print("  a rule that would fire on every clause is refused at load time")


# ---------------------------------------------------------------------------
# 5. segmentation
# ---------------------------------------------------------------------------

def test_segmentation():
    section("5  SEGMENTATION  (page furniture is not part of a clause)")

    a_pdf = _build_furniture_pdf()
    if a_pdf is None:
        print("  reportlab not installed, skipping the PDF test")
        return

    import pdfplumber
    with pdfplumber.open(a_pdf) as a_doc:
        as_pages = [p.extract_text() or "" for p in a_doc.pages]
    a_raw = "\n".join(as_pages)

    a_before = Path("/tmp/_v37_raw.txt")
    a_before.write_text(a_raw)

    with contextlib.redirect_stdout(io.StringIO()):
        as_old = segment.run(str(a_before), "loan", quiet=True)
        as_new = segment.run(a_pdf, "loan", quiet=True)

    def polluted(as_clauses):
        return sum(1 for c in as_clauses
                   if "Highly Confidential" in c.text or "Page " in c.text)

    print(f"  without the fix: {len(as_old)} clauses, {polluted(as_old)} carrying furniture")
    print(f"  with the fix:    {len(as_new)} clauses, {polluted(as_new)} carrying furniture")
    print("  the document has 6 clauses")
    check("segment", "page furniture removed from every clause", polluted(as_new), 0)
    check("segment", "clause count is right", len(as_new), 6)

    # The two shapes that defeated the old check, in isolation.
    a_footers = "\n".join(f"1. CLAUSE {i}\nBody of clause {i} which runs on for a while "
                          f"so that it is long enough to survive the minimum size "
                          f"filter applied by the segmenter.\n{i} Highly Confidential"
                          for i in range(1, 5))
    a_out = segment.strip_repeating_lines(a_footers)
    check("segment", "footers differing only by page number are recognised",
          "Highly Confidential" in a_out, False)
    print("  numbered footers are now recognised as one repeating footer")

    as_stripped = segment.strip_page_furniture([
        "Page 1 PRICING AND SERVICING\n1. DISBURSEMENT\nThe net disbursement shall be "
        "credited to the account.\n1 Confidential",
        "Page 2 TENURE AND DEFAULT\n2. REPAYMENT\nThe Borrower shall repay in "
        "instalments.\n2 Confidential"])
    a_joined = "\n".join(as_stripped)
    check("segment", "page banners carrying a page number are removed",
          "TENURE AND DEFAULT" in a_joined, False)
    check("segment", "the clauses themselves survive",
          "2. REPAYMENT" in a_joined and "1. DISBURSEMENT" in a_joined, True)
    print("  page banners are removed and the clauses under them survive")

    # Nothing may change on the fixtures.
    as_expected = {"clean_offer.txt": 8, "risky_offer.txt": 8, "clean_rent.txt": 7,
                   "risky_rent.txt": 8, "clean_loan.txt": 10, "risky_loan.txt": 8}
    a_changed = 0
    for a_name, a_want in as_expected.items():
        a_path = ROOT / "tests" / "fixtures" / a_name
        if not a_path.exists():
            continue
        a_type = ("employment" if "offer" in a_name
                  else "rental" if "rent" in a_name else "loan")
        with contextlib.redirect_stdout(io.StringIO()):
            as_c = segment.run(str(a_path), a_type, quiet=True)
        if len(as_c) != a_want:
            print(f"    CHANGED {a_name}: {a_want} -> {len(as_c)}")
            a_changed += 1
    print(f"  fixture clause counts unchanged: {len(as_expected) - a_changed}"
          f"/{len(as_expected)}")
    check("segment", "no regression on the fixtures", a_changed, 0)


def _build_furniture_pdf():
    """A two page PDF with the exact furniture the real documents carried."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas
    except ImportError:
        return None

    AS_PAGES = [
        ("PRICING AND SERVICING INTEREST, FEES AND PAYMENT ADMINISTRATION Clauses 1-3",
         [("1. DISBURSEMENT", "Subject to completion of the Lender's stated "
           "documentation requirements, the net disbursement shall be credited to the "
           "Borrower's nominated bank account after deduction of the Processing Fee."),
          ("2. REPAYMENT", "The Borrower shall repay the Principal and applicable "
           "interest through 36 monthly instalments. The due date is the 5th calendar "
           "day of each month."),
          ("3. APPLICATION OF PAYMENTS", "Unless required otherwise by applicable law, "
           "payments may be appropriated first to taxes and costs, then to overdue "
           "charges and interest, and finally to principal.")]),
        ("TENURE, DEFAULT AND EXIT TERM AND TERMINATION Clauses 4-6",
         [("4. PROCESSING FEE", "A Processing Fee equal to 2.25% of the Principal, plus "
           "applicable taxes, shall be payable at disbursement and may be deducted from "
           "the gross amount otherwise payable to the Borrower."),
          ("5. INTEREST CALCULATION", "Interest accrues on the outstanding principal at "
           "the stated annual rate using the Lender's standard daily accrual convention "
           "and actual days elapsed."),
          ("6. SET-OFF", "To the extent permitted by applicable law, the Lender may set "
           "off matured amounts due under this Agreement against credit balances "
           "otherwise payable by the Lender to the Borrower.")]),
    ]
    a_out = "/tmp/_v37_furniture.pdf"
    c = canvas.Canvas(a_out, pagesize=A4)
    a_w, a_h = A4
    for i, (a_banner, as_clauses) in enumerate(AS_PAGES, start=1):
        c.setFont("Helvetica", 8)
        c.drawString(20 * mm, a_h - 15 * mm, f"Page {i} {a_banner}")
        y = a_h - 30 * mm
        for a_head, a_body in as_clauses:
            c.setFont("Helvetica-Bold", 10)
            c.drawString(20 * mm, y, a_head)
            y -= 6 * mm
            c.setFont("Helvetica", 9)
            a_line = ""
            for w in a_body.split():
                if len(a_line + " " + w) > 95:
                    c.drawString(20 * mm, y, a_line)
                    y -= 5 * mm
                    a_line = w
                else:
                    a_line = (a_line + " " + w).strip()
            if a_line:
                c.drawString(20 * mm, y, a_line)
                y -= 10 * mm
        c.setFont("Helvetica", 7)
        c.drawString(20 * mm, 12 * mm,
                     f"{i} Highly Confidential - FICTIONAL SAMPLE - NOT A REAL CONTRACT")
        c.showPage()
    c.save()
    return a_out


# ---------------------------------------------------------------------------
# 6. end to end, through the real HTTP handler
# ---------------------------------------------------------------------------

class HostileExtractor:
    """
    A model that returns exactly what the real one returned on 17 September.

    It hands back loan facts for every clause it is shown, including clauses about
    educational qualifications and absence without permission. That is not a
    caricature: it is the transcript. The point of the test is that the pipeline
    now refuses those facts on its own, without the model improving.
    """

    def extract(self, doc_type, clause_text):
        as_params = {
            "loan": {"acceleration_without_notice": True,
                     "assignment_without_notice": True,
                     "jurisdiction_lender_city_only": True,
                     "penal_charge_amount_undisclosed": True},
            "rental": {"escalation_undefined": True},
            "employment": {},
        }.get(doc_type, {})
        return {"category": "boilerplate", "params": dict(as_params), "confidence": 1.0}


def test_end_to_end():
    section("6  END TO END  (the real POST /audit handler, not a layer beneath it)")

    # rglob, not glob. A real corpus is filed per document type in
    # data/corpus/employment, data/corpus/loan and data/corpus/rental, so a
    # non-recursive glob finds nothing and reports a populated corpus as absent.
    # retrieve.py has always used rglob; this check did not.
    if not list((ROOT / "data" / "corpus").rglob("*.txt")):
        print("  no statute corpus in data/corpus, so the agent cannot start.")
        print("  Run tools/fetch_corpus.py, then run this again.")
        print("  SKIPPED, and skipped is not passed.")
        AS_RESULTS.append(("end to end", "statute corpus present", False, "absent",
                           "present"))
        return

    import api
    import agent as agent_mod

    api._agent = agent_mod.ClauseAgent(extractor=HostileExtractor())

    a_letter = next(iter(sorted((ROOT / "tests" / "real").glob("*_full.txt"))),
                    ROOT / "tests" / "real" / "letter_full.txt")
    if not a_letter.exists():
        print("  tests/real is not present in this copy, using the fair offer fixture")
        a_letter = ROOT / "tests" / "fixtures" / "clean_offer.txt"
    a_text = a_letter.read_text(errors="ignore")

    print("  an employment offer letter, submitted as a Loan Agreement,")
    print("  with a model that returns loan facts for every clause it sees:")
    a_res = api._run(a_text, "loan")

    print(f"    type status      {a_res.type_status}")
    print(f"    suggested type   {a_res.type_suggested}")
    print(f"    findings         {sum(len(r.findings) for r in a_res.results)}")
    print(f"    high severity    {a_res.high}")
    print(f"    draft allowed    {a_res.draft_allowed}")

    check("end to end", "the wrong type is reported", a_res.type_status, "mismatch")
    check("end to end", "the right type is suggested", a_res.type_suggested, "employment")
    check("end to end", "no loan finding survives on an offer letter",
          sum(len(r.findings) for r in a_res.results), 0)
    check("end to end", "nothing is high severity", a_res.high, 0)
    check("end to end", "the negotiation draft is withheld", a_res.draft_allowed, False)
    check("end to end", "the reader is told why", bool(a_res.type_message), True)

    print("\n  the same letter, read as what it is:")
    a_res = api._run(a_text, "employment")
    print(f"    type status      {a_res.type_status}")
    print(f"    draft allowed    {a_res.draft_allowed}")
    check("end to end", "the correct type passes the guard", a_res.type_status, "ok")
    check("end to end", "and the draft is offered again", a_res.draft_allowed, True)

    print("\n  a fair rent agreement, read as a rent agreement,")
    print("  with a model that insists every clause is an uncapped rent revision:")
    a_rent = (ROOT / "tests" / "fixtures" / "clean_rent.txt").read_text(errors="ignore")
    a_res = api._run(a_rent, "rental")
    as_fired = [f.rule_id for r in a_res.results for f in r.findings]
    print(f"    findings         {len(as_fired)}  {sorted(set(as_fired))}")
    check("end to end", "R-RENT-09 does not fire on clauses that are not about rent",
          as_fired.count("R-RENT-09") <= 1, True)

    # A document-scope rule must appear once, not once per clause.
    as_rent10 = [f.rule_id for r in a_res.results for f in r.findings
                 if f.rule_id == "R-RENT-10"]
    print(f"    R-RENT-10 appears {len(as_rent10)} time(s) across "
          f"{a_res.clauses} clauses")
    check("end to end", "a document-scope finding is reported once",
          len(as_rent10) <= 1, True)


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 7. the two tools that close the remaining gaps
# ---------------------------------------------------------------------------

def test_tools():
    section("7  ANCHOR AND MEASUREMENT TOOLS")

    import importlib.util

    # --- anchor_fix ---
    a_engine = RuleEngine()
    as_unanchored = {r["id"] for t in a_engine.doc_types()
                     for r in a_engine.rules_for(t)
                     if r["basis"] != "norm" and not r.get("anchors")}

    a_spec = importlib.util.spec_from_file_location(
        "anchor_fix", ROOT / "tools" / "anchor_fix.py")
    a_mod = importlib.util.module_from_spec(a_spec)
    a_spec.loader.exec_module(a_mod)

    a_missing = as_unanchored - set(a_mod.AS_CANDIDATES)
    print(f"  grounded rules with no anchor: {len(as_unanchored)}")
    print(f"  of those, with candidate phrases ready: "
          f"{len(as_unanchored) - len(a_missing)}")
    if a_missing:
        print(f"    no candidates for: {sorted(a_missing)}")
    check("tools", "every unanchored rule has candidates", a_missing, set())

    # It has to use the verifier's own gate, not a copy of it. A second
    # implementation would drift and start writing anchors the verifier rejects.
    from agent import _anchor_present as a_real
    check("tools", "anchor_fix uses the verifier's own check",
          a_mod._anchor_present is a_real, True)
    print("  anchor_fix tests candidates with the verifier's own _anchor_present")

    # A candidate that is in the text passes, one that is not fails.
    A_S27 = ("27. Agreement in restraint of trade void. Every agreement by which any "
             "one is restrained from exercising a lawful profession, trade or business "
             "of any kind, is to that extent void.")
    check("tools", "a phrase in the text verifies",
          a_real(A_S27, ["restrained from exercising a lawful profession"]), True)
    check("tools", "a phrase not in the text does not",
          a_real(A_S27, ["quantum and reason for penal charges"]), False)
    print("  a candidate only passes when the words are actually in the span")

    # --- the measurement ---
    a_spec = importlib.util.spec_from_file_location(
        "evaluate", ROOT / "tools" / "evaluate.py")
    a_ev = importlib.util.module_from_spec(a_spec)
    sys.modules["evaluate"] = a_ev
    a_spec.loader.exec_module(a_ev)

    # The interval has to stay inside the unit interval at the ends, which is the
    # whole reason for using Wilson rather than the normal approximation. At 40/40
    # the textbook formula gives an upper bound above 100%.
    for a_h, a_n in [(40, 40), (0, 40), (36, 40), (8, 10), (1, 1)]:
        a_lo, a_hi = a_ev.wilson(a_h, a_n)
        a_ok = 0.0 <= a_lo <= a_hi <= 1.0 and a_lo <= a_h / a_n <= a_hi
        check("tools", f"wilson stays sane at {a_h}/{a_n}", a_ok, True)
    check("tools", "wilson handles n=0 without dividing by zero",
          a_ev.wilson(0, 0), (0.0, 0.0))
    a_lo40, a_hi40 = a_ev.wilson(32, 40)
    a_lo160, a_hi160 = a_ev.wilson(128, 160)
    check("tools", "more labels narrows the interval",
          (a_hi160 - a_lo160) < (a_hi40 - a_lo40), True)
    print(f"  80% at n=40 is {a_lo40:.0%} to {a_hi40:.0%}; at n=160 it is "
          f"{a_lo160:.0%} to {a_hi160:.0%}")

    # The provider gate. This asked for ANTHROPIC_API_KEY by name while llm.py
    # routes wherever LLM_PROVIDER points, so on a Groq setup the metric reported
    # "not measured" for a reason unrelated to labels or to the model.
    a_src = (ROOT / "tools" / "evaluate.py").read_text()
    a_body = a_src[a_src.index("def classification(doc_type)"):
                   a_src.index("def classification_all")]
    # Comments are stripped first. The commentary explaining the bug mentions the
    # key by name, and a test that reads prose rather than code would fail on the
    # explanation of the fix.
    a_code = "\n".join(ln for ln in a_body.split("\n")
                       if not ln.strip().startswith("#"))
    check("tools", "classification no longer reads one provider's key directly",
          "ANTHROPIC_API_KEY" in a_code, False)
    check("tools", "classification asks llm.available instead",
          "llm.available()" in a_body, True)
    print("  classification asks llm.available(), so it works on whatever "
          "provider is configured")

    check("tools", "a combined cross-type mode exists",
          hasattr(a_ev, "classification_all"), True)

    # --- anchor aware retrieval ---
    #
    # R-RENT-10 rests on section 17(1)(d) of the Registration Act. That section
    # runs 1779 characters in the real corpus and clause (d) begins at 973, so the
    # 900 character window stopped short of the only text supporting the rule. The
    # citation shown to the reader covered gifts and receipts and said nothing
    # about leases, and both other gates passed on it.
    from retrieve import _anchors_in
    from retrieve import StatuteIndex as A_Index

    A_SEC = ("17. Documents of which registration is compulsory. The following "
             "documents shall be registered, namely: " + ("(b) filler text. " * 60) +
             "and (d) leases of immovable property from year to year, or for any "
             "term exceeding one year, or reserving a yearly rent.")
    check("tools", "the anchor check finds a phrase that is present",
          _anchors_in(A_SEC, ["leases of immovable property from year to year"]), True)
    check("tools", "and rejects one that is not",
          _anchors_in(A_SEC[:600], ["leases of immovable property from year to year"]),
          False)
    check("tools", "retrieval widens rather than truncating",
          A_Index.AS_WINDOWS[0] < A_Index.AS_WINDOWS[-1], True)
    print("  retrieval widens the window until the span contains the rule's anchor")

    # Every grounded rule should now carry one, on a populated corpus.
    a_left = [r["id"] for t in a_engine.doc_types() for r in a_engine.rules_for(t)
              if r["basis"] != "norm" and not r.get("anchors")]
    print(f"  grounded rules still with no anchor: {len(a_left)}"
          f"{' ' + str(a_left) if a_left else ''}")


# ---------------------------------------------------------------------------
# 8. v37.3: found by running the real documents through the live model
# ---------------------------------------------------------------------------

def test_v373():
    section("8  v37.3  (defects found on 20 September against the live model)")

    import importlib
    importlib.reload(deterministic)
    importlib.reload(docscan)
    importlib.reload(segment)

    # --- the harassment clause -------------------------------------------------
    # R-EMP-10 fired on an internship letter's POSH clause because its note ended
    # "termination with immediate effect", and the draft asked the employer to add a
    # cure period for sexual harassment. Both roads to that fact must be closed.
    A_POSH = ("5.2 Prevention of Sexual Harassment at work place: The Company will have "
              "zero tolerance towards sexual harassment. The workplace includes b) All "
              "company-related activities performed at any other site. Sexual Harassment "
              "at the workplace includes: 1. unwelcome sexual advances, 2. demand or "
              "request for sexual favours. Note: If any of the interns are found to be "
              "involved in sexual harassment, it would result in termination with "
              "immediate effect.")
    a_p, _ = deterministic.read_facts("employment", A_POSH)
    check("v37.3", "reader leaves a harassment clause alone",
          bool(a_p.get("cause_undefined_or_vague")), False)
    _, a_dropped = deterministic.apply_gates(
        "employment", A_POSH, {"cause_undefined_or_vague": True})
    check("v37.3", "and so does the model road", len(a_dropped), 1)
    print("  a harassment clause is never flagged as broad cause, on either road")

    # "performed" in the definition of the workplace is a verb, not a ground.
    check("v37.3", "'activities performed' is not a vague ground",
          deterministic.protected_misconduct(A_POSH), True)

    # But harassment alongside a genuinely vague ground is still a fair flag.
    for a_label, a_text in [
        ("performance", "Sexual harassment or poor performance will result in "
                        "termination with immediate effect."),
        ("prejudicial", "Sexual harassment or conduct prejudicial to the interest of "
                        "the Company shall result in termination forthwith."),
        ("sole discretion", "The Company may terminate your employment with immediate "
                            "effect at its sole discretion."),
    ]:
        a_p, _ = deterministic.read_facts("employment", a_text)
        check("v37.3", f"vague ground still flags: {a_label}",
              bool(a_p.get("cause_undefined_or_vague")), True)
    print("  harassment plus a genuinely vague ground is still flagged")

    # --- the term of a lease ---------------------------------------------------
    # A real 12-month lease stated its term as a table row with no "of", so
    # R-RENT-10 could not see that a registrable lease was unregistered.
    for a_label, a_text, a_want in [
        ("key-terms table", "Term 01 October 2026 to 30 September 2027 (12 months)", 12),
        ("11 (eleven) months", "for a period of 11 (eleven) months commencing", 11),
        ("eleven (11) months", "for a period of eleven (11) months commencing", 11),
        ("in years", "The term of this lease is 3 years from the date", 36),
        ("lease period row", "Lease Period: 24 months", 24),
        ("lock-in is not the term", "After a minimum commitment of six months", None),
    ]:
        check("v37.3", f"term: {a_label}", docscan._term_months(a_text)[0], a_want)
    print("  the lease term is read in every common form, and a lock-in is not mistaken for it")

    # --- section banners -------------------------------------------------------
    A_BANNER = ("3. Application of Payments\nPayments may be appropriated first to costs.\n"
                "PRICING AND SERVICING\nINTEREST, FEES AND PAYMENT ADMINISTRATION\n"
                "Clauses 4-10 | Rate, fees, prepayment, statements and mandates\n"
                "4. Processing Fee\nA Processing Fee equal to 2.25% shall be payable.")
    a_out = segment.strip_section_banners(A_BANNER)
    check("v37.3", "navigation banner removed", "Clauses 4-10" in a_out, False)
    check("v37.3", "its title lines removed", "PRICING AND SERVICING" in a_out, False)
    check("v37.3", "the clauses either side survive",
          "3. Application" in a_out and "4. Processing Fee" in a_out, True)
    # A capital heading with no navigation line under it is a real heading.
    A_REAL_CAPS = "SECURITY DEPOSIT\nThe Tenant shall pay a deposit of two months rent."
    check("v37.3", "a lone capital heading is never removed",
          segment.strip_section_banners(A_REAL_CAPS), A_REAL_CAPS)
    print("  section banners are removed, and ordinary capital headings are not")

    # --- mixed numbering -------------------------------------------------------
    # "12.4." for most clauses and plain "13." for short ones. Every plain section
    # vanished into the clause above it, including an exclusive-jurisdiction term.
    A_MIXED = "\n".join([
        "12. Termination",
        "12.1. This letter shall expire on the last day of the internship period.",
        "12.4. The Company reserves the right to terminate forthwith for misconduct.",
        "13. Notices",
        "All communications shall be served at the address on the first page.",
        "17. Governing Law and Jurisdiction",
        "The Courts at Pune shall have jurisdiction to the exclusion of all others.",
    ])
    as_b = segment.split_dotted_mixed(A_MIXED.split("\n"))
    check("v37.3", "a plain section after dotted ones is its own clause",
          any(b.startswith("13. Notices") for b in as_b), True)
    check("v37.3", "a skipped number is not taken as a heading",
          any(b.startswith("17. Governing") for b in as_b), False)
    check("v37.3", "a bare section heading sits over the clause it heads",
          any(b.startswith("12. Termination\n12.1.") for b in as_b), True)

    # The harassment clause's own numbered list must not be cut into clauses.
    A_LIST = "\n".join([
        "5.2 Prevention of Sexual Harassment at work place:",
        "Sexual Harassment at the workplace includes:",
        "1. unwelcome sexual advances (verbal, written or physical),",
        "6. Implied or explicit promise of preferential treatment in employment,",
        "11. humiliating treatment likely to affect an individual's health or safety.",
        "6. Confidential Information and Non-Disclosure",
        "6.1. The Candidate is aware that information may be disclosed to him.",
    ])
    as_b = segment.split_dotted_mixed(A_LIST.split("\n"))
    a_posh = [b for b in as_b if b.startswith("5.2")]
    check("v37.3", "a numbered list inside a clause is not split into clauses",
          bool(a_posh) and "1. unwelcome" in a_posh[0] and "11. humiliating" in a_posh[0], True)
    check("v37.3", "the real next section still starts its own clause",
          any("6. Confidential" in b and "6.1." in b for b in as_b), True)
    print("  plain sections split out, lists inside a clause stay whole")

    # --- v37.4: a fee table is not furniture ------------------------------------
    # v37 masked the digits of every line when looking for repeated footers, so
    # "Late fee Rs. 500" and "Late fee Rs. 750" became the same line and both were
    # deleted. Only lines that read like furniture are compared with digits masked.
    a_fees = "Late fee Rs. 500\nLate fee Rs. 750\nThe Borrower shall pay the charges above."
    check("v37.3", "a fee table survives furniture stripping",
          segment.strip_repeating_lines(a_fees), a_fees)
    a_emi = ("Instalment 1 due 05-10-2026 Rs. 30,090\nInstalment 2 due 05-11-2026 "
             "Rs. 30,090\nThe Borrower shall pay each instalment.")
    check("v37.3", "an EMI schedule survives furniture stripping",
          segment.strip_repeating_lines(a_emi), a_emi)
    check("v37.3", "a numbered confidentiality footer is still removed",
          "Confidential" in segment.strip_repeating_lines(
              "A.\n1 Highly Confidential\nB.\n2 Highly Confidential"), False)
    print("  fee tables and schedules survive; numbered footers still go")

    # --- the clause ceiling ----------------------------------------------------
    import api
    check("v37.3", "a correctly segmented 48-clause letter is not refused",
          api.MAX_CLAUSES >= 48, True)
    print(f"  clause ceiling is {api.MAX_CLAUSES}")

    # --- anchors shipped -------------------------------------------------------
    a_eng = RuleEngine()
    a_left = [r["id"] for t in a_eng.doc_types() for r in a_eng.rules_for(t)
              if r["basis"] != "norm" and not r.get("anchors")]
    check("v37.3", "every grounded rule ships with its anchor", a_left, [])


def test_v375():
    section("9  v37.5  (the page said nothing was stored, and every audit was saved)")
    import tempfile

    a_clause_dir = ROOT / "data" / "clauses"
    a_fixture = ROOT / "tests" / "fixtures" / "risky_offer.txt"
    a_text = a_fixture.read_text(encoding="utf-8")

    # --- segment.run no longer saves unless asked ------------------------------
    # A probe with a name nothing else uses. Running a fixture would only overwrite
    # the fixture's own .jsonl, which a before-and-after listing cannot see.
    with tempfile.TemporaryDirectory() as a_dir:
        a_probe = Path(a_dir) / "employment_zz_probe.txt"
        a_probe.write_text(a_text, encoding="utf-8")
        a_saved = a_clause_dir / "employment_zz_probe.jsonl"
        a_saved.unlink(missing_ok=True)

        segment.run(a_probe, "employment", quiet=True)
        check("v37.5", "segment.run() writes nothing by default", a_saved.exists(), False)

        segment.run(a_probe, "employment", quiet=True, save=True)
        check("v37.5", "the corpus builder can still save when it asks", a_saved.exists(), True)
        a_saved.unlink(missing_ok=True)
    print("  segment.run saves only when asked")

    # --- in memory splits exactly as from a file -------------------------------
    as_from_file = segment.run(a_fixture, "employment", quiet=True)
    as_from_text, _how, _n = segment.split_text(a_text, "employment", "x")
    check("v37.5", "text in memory splits exactly as the same text from a file",
          [(c.text, c.char_start, c.char_end) for c in as_from_text],
          [(c.text, c.char_start, c.char_end) for c in as_from_file])
    check("v37.5", "Windows line endings read the same from memory as from a file",
          segment.read_bytes(a_text.replace("\n", "\r\n").encode(), ".txt"), a_text)
    print("  a document splits the same whichever way it arrives")

    # --- the real endpoints leave nothing behind -------------------------------
    # The model is stubbed so this runs without a provider or the corpus. Everything
    # it stands in for comes after segmentation, which is where the copies were made.
    import api
    from fastapi.testclient import TestClient

    class _Stub:
        def __init__(self):
            self.engine, self.extractor, self.router = RuleEngine(), object(), None

        def run(self, doc_type, text, clause_id=None, **kw):
            return {"category": "boilerplate", "findings": [], "params": {}, "skipped": False}

    a_pdf = _build_furniture_pdf()
    a_tmpdir = Path(tempfile.gettempdir())

    def _leftovers():
        as_repo = {p for p in (ROOT / "data").rglob("*") if p.is_file()}
        as_tmp = {p for p in a_tmpdir.glob("tmp*") if p.suffix in (".txt", ".pdf", ".docx")}
        return as_repo | as_tmp

    a_real_agent, a_real_fn = api._agent, api.agent
    a_stub = _Stub()
    api._agent, api.agent = a_stub, (lambda: a_stub)
    try:
        c = TestClient(api.app, raise_server_exceptions=False)
        as_start = _leftovers()
        as_codes = [
            c.post("/audit", json={"text": a_text, "doc_type": "employment"}).status_code,
            c.post("/audit/upload?doc_type=employment",
                   files={"file": ("offer.txt", a_text.encode(), "text/plain")}).status_code,
        ]
        if a_pdf:
            as_codes.append(c.post("/audit/upload?doc_type=loan", files={
                "file": ("loan.pdf", Path(a_pdf).read_bytes(), "application/pdf")}).status_code)
        as_left = sorted(str(p) for p in _leftovers() - as_start)
    finally:
        api._agent, api.agent = a_real_agent, a_real_fn

    check("v37.5", "every upload path answered", set(as_codes), {200})
    check("v37.5", "an audit leaves no copy in the repo or the temp directory", as_left, [])
    print(f"  {len(as_codes)} audits through the real endpoints, nothing left on disk")

    # --- the recorder groups findings exactly as the page does ------------------
    # A recorded sample's draft has to be the draft a visitor would have got, so
    # tools/record_samples.py copies the page's grouping. It was compared line for
    # line against the page's own JavaScript in node. If the page's version changes,
    # this hash changes, and the copy has to be brought back in step.
    import hashlib
    sys.path.insert(0, str(ROOT / "tools"))
    import record_samples
    a_page = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    a_block = a_page[a_page.index("const SEV_RANK"):a_page.index("g.where.push")]
    check("v37.5", "the page's grouping is still the one the recorder copies",
          hashlib.sha1(a_block.encode()).hexdigest()[:12], "f0eeac3d81e8")

    def _c(i, fs):
        return {"index": i, "heading": f"h{i}", "text": f"t{i}", "findings": fs}
    as_g = record_samples.group_issues({"results": [
        _c(3, [{"rule_id": "A", "severity": "medium", "evidence": ["terminated forthwith."]}]),
        _c(5, [{"rule_id": "A", "severity": "high", "evidence": ["terminated forthwith", "x"]}]),
        _c(7, [{"rule_id": "A", "severity": None, "evidence": None},
               {"rule_id": "B", "severity": "low", "evidence": []}]),
    ]})
    check("v37.5", "one issue per rule, worst severity, punctuation-only repeats merged",
          [(g["rule_id"], g["severity"], g["evidence"], [w["ci"] for w in g["where"]])
           for g in as_g],
          [("A", "high", ["terminated forthwith.", "x"], [3, 5, 7]), ("B", "low", [], [7])])
    print("  the recorder asks for the draft the page would have asked for")


def test_v376():
    section("10  v37.6  (the live runs of 23 September)")
    import negotiate
    from deterministic import read_more_facts
    a_engine = RuleEngine()

    def facts(t):
        return read_more_facts("employment", t)[0]

    # --- the reader --------------------------------------------------------------
    check("v37.6", "a duty limited to non-public information has its carve-out",
          facts("You shall, during and after your employment, keep confidential all "
                "non-public information relating to the Company.").get("perpetual_confidentiality"),
          None)
    check("v37.6", "confidentiality with no carve-out is still flagged",
          facts("You shall at all times, during and after your employment, keep "
                "confidential all information relating to the Company.").get("perpetual_confidentiality"),
          True)
    check("v37.6", "a background-check clause is not a broad termination ground",
          facts("Failure to submit documents on time, or unsatisfactory check results, may "
                "lead to withdrawal of the offer or termination of employment, at the "
                "Company's sole discretion.").get("cause_undefined_or_vague"), None)
    check("v37.6", "terminating for policy violations or poor performance is one",
          facts("The Company also reserves the right to terminate employment for policy "
                "violations or unsatisfactory performance.").get("cause_undefined_or_vague"), True)
    check("v37.6", "a non-compete that runs from termination is not a termination ground",
          facts("For twelve months following the termination of your employment for any "
                "reason whatsoever, you shall not compete with the Company.").get("cause_undefined_or_vague"),
          None)
    check("v37.6", "a performance ground with notice is left alone",
          facts("The Company may terminate your employment for unsatisfactory performance "
                "by giving thirty days' notice.").get("cause_undefined_or_vague"), None)
    print("  carve-outs, background checks and termination grounds read correctly")

    # --- working hours: the cap is on working time, not time on the premises -------
    a_week = facts("Working Days: Monday - Saturday Work Timings: 11:00AM - 8:00PM")
    check("v37.6", "nine hours across six days spans 54, and is 48 after the rest hour",
          (a_week.get("weekly_hours"), a_week.get("weekly_hours_net")), (54.0, 48.0))
    as_sev = [f.severity for f in a_engine.evaluate("employment", a_week) if f.rule_id == "R-EMP-16"]
    check("v37.6", "so it is raised, but not as high", as_sev, ["medium"])
    a_long = facts("Working Days: Monday - Saturday Work Timings: 9:00AM - 7:00PM")
    as_sev = [f.severity for f in a_engine.evaluate("employment", a_long) if f.rule_id == "R-EMP-16"]
    check("v37.6", "ten hours across six days is over 48 even after it, so high", as_sev, ["high"])
    print("  hours are judged as working time")

    # --- R-RENT-10 says what its passage says ------------------------------------
    as_f = a_engine.evaluate("rental", {"doc__term_months": 12, "doc__registration_mentioned": False})
    check("v37.6", "R-RENT-10 silent on exactly twelve months: the Act says exceeding one year",
          [f.rule_id for f in as_f if f.rule_id == "R-RENT-10"], [])
    print("  registration fires above one year, as section 17 reads")

    # --- the draft never quotes what the contract does not say -------------------
    as_fnd = [{"rule_id": "R-LOAN-10", "severity": "medium", "message": "m", "basis": "norm",
               "heading": "8. Prepayment",
               "clause_text": "The Lender may charge 5% of the prepaid principal for "
                              "prepayment before completion of 12 months."}]
    a_fake = ("The clause states that the lender \u201cmay recover any amount at any time "
              "without notice to the borrower\u201d.")
    a_real = ("Clause 8 reads \u201cThe Lender may charge 5% of the prepaid principal for "
              "prepayment\u201d.")
    check("v37.6", "an invented quote in curly quotation marks is caught",
          any("not in the contract" in x for x in negotiate.validate_draft(a_fake, as_fnd, "loan")), True)
    check("v37.6", "a real quote in curly quotation marks passes",
          any("not in the contract" in x for x in negotiate.validate_draft(a_real, as_fnd, "loan")), False)
    a_prompt = negotiate.build_prompt("rental", [{
        "rule_id": "R-RENT-10", "severity": "medium", "basis": "statutory",
        "message": "A lease for more than one year must be registered.",
        "heading": "Preamble", "clause_text": "RESIDENTIAL RENTAL AGREEMENT made on..."}], "firm")
    check("v37.6", "a whole-agreement finding is given no clause to quote",
          ("The clause says" in a_prompt, "agreement as a whole" in a_prompt), (False, True))
    print("  the draft cannot put words in the contract's mouth")


def test_v377():
    section("11  v37.7  (the live runs of 24 September)")
    import docscan
    import negotiate
    from deterministic import read_more_facts
    a_engine = RuleEngine()

    def dep(t):
        return docscan._deposit_months(t)[0]

    # --- the deposit, read off the page ----------------------------------------
    check("v37.7", "deposit stated in a key-terms row as months of rent",
          dep("Monthly Rent INR 31,500 payable monthly\n"
              "Security Deposit INR 94,500 (equivalent to three months' rent)"), 3)
    check("v37.7", "deposit stated only in money, divided by the monthly rent",
          dep("Monthly Rent INR 30,000 payable by the 5th.\nSecurity Deposit INR 90,000."), 3.0)
    check("v37.7", "a maintenance charge is not read as the rent",
          dep("Monthly Rent INR 30,000.\nMaintenance INR 3,000 per month.\n"
              "Security Deposit INR 60,000."), 2.0)
    check("v37.7", "two different rents and no months stated gives no answer",
          dep("Monthly Rent INR 30,000.\nFrom year two the rent shall be INR 33,000 "
              "per month.\nSecurity Deposit INR 90,000."), None)
    as_p, _ = docscan.read_document("rental",
        "Term 01 October 2026 to 30 September 2027 (12 months)\n"
        "Monthly Rent INR 31,500\nSecurity Deposit INR 94,500 (equivalent to three "
        "months' rent)")
    check("v37.7", "R-RENT-01 fires from the page alone, once, as high",
          [(f.rule_id, f.severity) for f in a_engine.evaluate("rental", as_p)
           if f.rule_id == "R-RENT-01"], [("R-RENT-01", "high")])
    check("v37.7", "and stays silent at two months",
          [f.rule_id for f in a_engine.evaluate("rental", {"doc__deposit_months": 2})
           if f.rule_id == "R-RENT-01"], [])
    print("  the deposit is read from the page, not left to the model")

    # --- evidence and drafts ------------------------------------------------------
    as_f, as_e = read_more_facts("employment",
        "Serving the notice period is mandatory to receive an experience certificate. "
        "The Company also reserves the right to terminate employment for policy "
        "violations or unsatisfactory performance.")
    check("v37.7", "broad-cause evidence starts at its own sentence",
          (as_e.get("cause_undefined_or_vague") or "").startswith("The Company"), True)
    a_norm = negotiate.build_prompt("employment", [{
        "rule_id": "R-EMP-10", "severity": "medium", "basis": "norm", "message": "m",
        "heading": "h", "clause_text": "The Company may terminate forthwith."}], "firm")
    check("v37.7", "a draft point with no law behind it cites none and concedes nothing",
          ("No statute settles" in a_norm, "No law applies" in a_norm), (False, True))
    print("  evidence opens cleanly; drafts ask on the merits")


def main():
    print("Clarity v37 regression suite")
    print("Every test below is a defect that reached a real document.")

    test_guard()
    test_gates()
    test_docscan()
    test_rules()
    test_segmentation()
    test_end_to_end()
    test_tools()
    test_v373()
    test_v375()
    test_v376()
    test_v377()

    section("VERDICT")
    as_by_section = {}
    for a_sec, a_name, a_ok, a_got, a_want in AS_RESULTS:
        a_p, a_f = as_by_section.get(a_sec, (0, 0))
        as_by_section[a_sec] = (a_p + (1 if a_ok else 0), a_f + (0 if a_ok else 1))

    for a_sec, (a_p, a_f) in as_by_section.items():
        a_mark = "PASS" if a_f == 0 else f"FAIL ({a_f})"
        print(f"  {a_sec:<14} {a_p:>3} passed   {a_mark}")

    as_failed = [(s, n, g, w) for s, n, ok, g, w in AS_RESULTS if not ok]
    if as_failed:
        print(f"\n  {len(as_failed)} failing:")
        for a_sec, a_name, a_got, a_want in as_failed:
            print(f"    [{a_sec}] {a_name}")
            print(f"        got  {a_got!r}")
            print(f"        want {a_want!r}")
        print("\n  NOT READY.")
        return 1

    print(f"\n  {len(AS_RESULTS)} checks, all passing.")
    print("\n  This suite covers the v37 corrections only. It does not measure whether")
    print("  the rules are right, and it does not measure recall on unseen documents.")
    print("  Run tools/selftest.py and tools/check_corpus.py as well.")
    print("\n  Flags for review, not legal advice.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
