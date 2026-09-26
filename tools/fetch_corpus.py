"""
Downloads the grounding corpus. Run once, takes about a minute.

    python tools/fetch_corpus.py

All six are official bare Acts from India Code (indiacode.nic.in), the Government
of India statute repository. Free, public, no licence issue. URLs verified
12 August 2026. If one 404s, search the Act name on indiacode.nic.in and swap the
URL in SOURCES below.
"""

from pathlib import Path
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "data" / "corpus"

SOURCES = {
    "rental": [
        ("transfer_of_property_act_1882", [
         "https://www.indiacode.nic.in/bitstream/123456789/2338/1/A1882-04.pdf",
         "https://indiacode.gov.in/bitstream/123456789/2338/1/A1882-04.pdf"]),
        ("registration_act_1908", [
         "https://www.indiacode.nic.in/bitstream/123456789/13236/1/the_registration_act,_1908.pdf",
         "https://www.indiacode.nic.in/bitstream/123456789/15937/1/the_registration_act,1908.pdf",
         "https://www.indiacode.nic.in/bitstream/123456789/5753/1/indian_registration_act_1908_searchable.pdf",
         "https://indiacode.gov.in/bitstream/123456789/13236/1/the_registration_act,_1908.pdf"]),
        ("indian_stamp_act_1899", [
         "https://www.indiacode.nic.in/bitstream/123456789/15510/5/A1899-2%20.pdf",
         "https://www.indiacode.nic.in/bitstream/123456789/2265/1/A1899-02.pdf",
         "https://indiacode.gov.in/bitstream/123456789/2265/1/A1899-02.pdf"]),
    ],
    "loan": [
        ("indian_contract_act_1872", [
         "https://www.indiacode.nic.in/bitstream/123456789/2187/2/A187209.pdf",
         "https://indiacode.gov.in/bitstream/123456789/2187/2/A187209.pdf"]),
    ],
    "employment": [
        ("indian_contract_act_1872", [
         "https://www.indiacode.nic.in/bitstream/123456789/2187/2/A187209.pdf",
         "https://indiacode.gov.in/bitstream/123456789/2187/2/A187209.pdf"]),
        ("specific_relief_act_1963", [
         "https://www.indiacode.nic.in/bitstream/123456789/1583/7/A1963-47.pdf",
         "https://indiacode.gov.in/bitstream/123456789/1583/7/A1963-47.pdf"]),
        ("copyright_act_1957", [
         "https://copyright.gov.in/Documents/Copyrightrules1957.pdf",
         "https://odishapolice.gov.in/sites/default/files/PDF/The%20Indian%20Copyright%20Act%201957.pdf",
         "https://www.indiacode.nic.in/bitstream/123456789/15356/1/the_copyright_act,_1957.pdf",
         "https://indiacode.gov.in/bitstream/123456789/15356/1/the_copyright_act,_1957.pdf",
         "https://www.indiacode.nic.in/bitstream/123456789/1367/1/A195714.pdf"]),
    ],
}



UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"


def grab(as_urls, out_path):
    """Try each mirror in turn. Single hardcoded links to India Code rot: the site
    reorganises bitstream IDs and has migrated domain once already, so one dead URL
    should not cost a statute the rules depend on."""
    if isinstance(as_urls, str):
        as_urls = [as_urls]
    if out_path.exists() and out_path.stat().st_size > 1000:
        print(f"  skip (have it)  {out_path.name}")
        return True
    as_last = None
    for as_i, as_url in enumerate(as_urls):
        if _try_one(as_url, out_path, quiet=(as_i < len(as_urls) - 1)):
            return True
        as_last = as_url
    print(f"  FAILED  {out_path.name}  ->  all {len(as_urls)} sources dead")
    as_blocks = BLOCKS.get(out_path.stem, "")
    if as_blocks:
        print(f"      This blocks: {as_blocks}")
    print("      India Code reorganises its bitstream IDs and has migrated domain,")
    print("      so these links rot. Fix it by hand, it takes a minute:")
    print("        1. open  https://www.indiacode.gov.in  and search the Act by name")
    print("        2. download the PDF")
    print(f"        3. save it exactly as:  {out_path}")
    print("        4. re-run this script. It will skip the download and extract text.")
    return False


# Which rules stop working when a statute is missing. A generic "some rules are
# withheld" tells you nothing about whether the self-test will now fail.
BLOCKS = {
    "copyright_act_1957": "R-EMP-05 and R-EMP-06. R-EMP-05 is a REQUIRED employment "
                          "finding, so selftest.py will fail employment without this",
    "registration_act_1908": "R-RENT-10 only, which is optional. selftest still passes",
    "indian_stamp_act_1899": "nothing. No rule cites it",
}


def _try_one(as_url, out_path, quiet=False):
    try:
        req = urllib.request.Request(as_url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
        if out_path.suffix == ".pdf" and not data[:5].startswith(b"%PDF"):
            if not quiet:
                print(f"  not a PDF, an error page was served  {out_path.name}")
            return False
        if len(data) < 20000:
            if not quiet:
                print(f"  too small, probably an error page  {out_path.name}")
            return False
        out_path.write_bytes(data)
        print(f"  ok  {out_path.name}  ({len(data)//1024} KB)")
        return True
    except Exception as e:
        if not quiet:
            print(f"  FAILED  {out_path.name}  ->  {e}")
            if "403" in str(e):
                print("      403 usually means a network egress block, not a bad URL.")
        return False


def to_text(pdf_path):
    """PDF to plain text, so retrieval in Phase 2 has something clean to chunk."""
    try:
        import pdfplumber
    except ImportError:
        print("  pdfplumber not installed, skipping text extraction")
        return
    a_out = pdf_path.with_suffix(".txt")
    if a_out.exists():
        return
    try:
        with pdfplumber.open(pdf_path) as pdf:
            as_pages = [p.extract_text() or "" for p in pdf.pages]
    except Exception as e:
        # One unreadable download should not kill the run and leave every later
        # statute unfetched. Bin the bad file so a re-run retries it cleanly.
        print(f"      not readable as a PDF ({type(e).__name__}), discarding it")
        pdf_path.unlink(missing_ok=True)
        return
    a_text = "\n\n".join(as_pages)
    if len(a_text) < 5000:
        print(f"      only {len(a_text)} chars extracted, discarding as an error page")
        pdf_path.unlink(missing_ok=True)
        return
    a_out.write_text(a_text, encoding="utf-8")
    print(f"      -> {a_out.name}  ({len(a_text)//1000}k chars)")


RBI_CIRCULAR = ("rbi_penal_charges_2023",
                "https://rbi.org.in/Scripts/NotificationUser.aspx?Id=12527&Mode=0")

# The Model Tenancy Act is a model law from the Ministry of Housing rather than a
# central Act, so it is not on India Code. Sources are tried in order.
#
# The ministry's own text comes first. PRS used to, and it is a summary, not the
# Act: it has no section numbers, so rules could only cite a vague "provision", and
# a research group's page furniture was once retrieved and displayed to a user as
# the law a finding rested on. Worse, a correct rule about the twenty-four hour
# entry notice in section 17 was deleted during verification on the grounds that
# "the Act does not say this", because the summary does not mention it. The Act
# does. Checking a claim against a summary is how a true rule gets deleted and a
# false note gets shipped.
#
# PRS stays last as a degraded fallback, and the result is labelled when it is used.
MTA_SOURCES = [
    ("mohua", "http://mohua.gov.in/upload/uploadfiles/files/Model-Tenancy-Act-English-02_06_2021.pdf"),
    ("mohua-https", "https://mohua.gov.in/upload/uploadfiles/files/Model-Tenancy-Act-English-02_06_2021.pdf"),
    ("instapdf", "https://instapdf.in/download-free/?pdfid=31412"),
    ("ibclaw", "https://ibclaw.in/model-tenancy-act-2021/"),
    ("prs", "https://prsindia.org/billtrack/the-model-tenancy-act-2021"),
]

# PRS's summary is about 26k characters. The Act itself extracts to roughly 54k, so
# a 60k floor rejected the real Act as if it were a summary. Length alone is a poor
# test anyway: what actually distinguishes them is that the Act carries numbered
# sections and a summary does not, which is exactly what the rules need to cite.
MTA_MIN_CHARS = 35000
MTA_REQUIRED_SECTIONS = ("11", "15", "17")


def looks_like_the_act(text):
    """Numbered sections the rules cite, present as section markers."""
    import re as _re
    return all(_re.search(rf"(?m)^\s*{n}\.\s", text) for n in MTA_REQUIRED_SECTIONS)


def strip_html(html):
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for t in soup(["script", "style", "nav", "header", "footer"]):
            t.decompose()
        text = soup.get_text("\n")
    except ImportError:
        import re as _re
        text = _re.sub(r"<[^>]+>", "\n", html)
    import re as _re
    return _re.sub(r"\n\s*\n\s*\n+", "\n\n",
                   _re.sub(r"[ \t]+", " ", text)).strip()


def grab_mta():
    """Model Tenancy Act 2021. Four rental rules cite it, so this is not optional."""
    a_dir = CORPUS / "rental"
    a_dir.mkdir(parents=True, exist_ok=True)
    a_out = a_dir / "model_tenancy_act_2021.txt"
    if a_out.exists() and a_out.stat().st_size > MTA_MIN_CHARS:
        print(f"  skip (have it)  {a_out.name}")
        return True
    if a_out.exists():
        print(f"  {a_out.name} on disk is only {a_out.stat().st_size//1000}k, which is a"
              f" summary rather than the Act. Refetching.")
        a_out.unlink()

    # A manually downloaded PDF, placed here by hand. Every automated source for this
    # Act is dead or serves a summary, and the other statutes already support this
    # route because grab() checks for an existing file first. This one did not, so
    # dropping the PDF in had no effect and the manual instruction did not work.
    a_manual = a_dir / "model_tenancy_act_2021.pdf"
    if a_manual.exists() and a_manual.stat().st_size > 20000:
        print(f"  found a manually saved {a_manual.name}, extracting")
        try:
            import pdfplumber
            with pdfplumber.open(a_manual) as pdf:
                a_text = "\n\n".join(pg.extract_text() or "" for pg in pdf.pages)
        except ImportError:
            print("      pdfplumber not installed, cannot extract. Activate the venv.")
            a_text = ""
        except Exception as e:
            print(f"      not readable as a PDF ({type(e).__name__}), discarding it")
            a_manual.unlink(missing_ok=True)
            a_text = ""
        if len(a_text) >= MTA_MIN_CHARS and looks_like_the_act(a_text):
            a_out.write_text(a_text, encoding="utf-8")
            print(f"  ok  {a_out.name}  ({len(a_text)//1000}k chars, from your download)")
            return True
        if a_text:
            print(f"      {len(a_text)//1000}k chars extracted, but "
                  + ("it has no numbered sections 11, 15 and 17, so rules cannot cite it"
                     if len(a_text) >= MTA_MIN_CHARS else
                     "that is too short to be the Act"))

    for name, a_url in MTA_SOURCES:
        try:
            req = urllib.request.Request(a_url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
        except Exception as e:
            print(f"  {name} failed ({e})")
            continue

        if a_url.endswith(".pdf"):
            a_pdf = a_dir / "model_tenancy_act_2021.pdf"
            a_pdf.write_bytes(raw)
            try:
                import pdfplumber
                with pdfplumber.open(a_pdf) as pdf:
                    text = "\n\n".join(p.extract_text() or "" for p in pdf.pages)
            except Exception as e:
                print(f"  {name}: downloaded but could not read the PDF ({e})")
                continue
        else:
            text = strip_html(raw.decode("utf-8", errors="ignore"))

        if "tenancy" not in text.lower():
            print(f"  {name}: content did not look like the Act, trying the next source")
            continue

        if len(text) < MTA_MIN_CHARS or not looks_like_the_act(text):
            print(f"  {name}: {len(text)//1000}k chars without the numbered sections the"
                  f" rules cite. That is a summary, not the Act. Trying the next source.")
            continue

        a_out.write_text(text, encoding="utf-8")
        print(f"  ok  {a_out.name}  ({len(text)//1000}k chars, via {name})")
        if "section 17" not in text.lower() and "17." not in text:
            print("      warning: section 17 not found in the retrieved text, so the"
                  " entry-notice rule will be withheld")
        return True

    print("  FAILED  model_tenancy_act_2021.txt")
    print("      Four rental rules cite this Act and will be withheld without it.")
    print("      Save the text by hand from:")
    for _, u in MTA_SOURCES:
        print(f"        {u}")
    print(f"      into {a_out}")
    return False


def grab_rbi():
    """
    The RBI penal charges circular is an HTML notification rather than a PDF, so it
    is fetched and stripped separately. Verified live on rbi.org.in.
    """
    a_dir = CORPUS / "loan"
    a_dir.mkdir(parents=True, exist_ok=True)
    a_out = a_dir / f"{RBI_CIRCULAR[0]}.txt"
    if a_out.exists() and a_out.stat().st_size > 1000:
        print(f"  skip (have it)  {a_out.name}")
        return True
    try:
        req = urllib.request.Request(RBI_CIRCULAR[1], headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            html = r.read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"  FAILED  {a_out.name}  ->  {e}")
        print(f"      open this in a browser and save the text:\n      {RBI_CIRCULAR[1]}")
        return False

    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for t in soup(["script", "style"]):
            t.decompose()
        text = soup.get_text("\n")
    except ImportError:
        import re as _re
        text = _re.sub(r"<[^>]+>", "\n", html)

    import re as _re
    text = _re.sub(r"\n\s*\n\s*\n+", "\n\n", _re.sub(r"[ \t]+", " ", text)).strip()
    a_out.write_text(text, encoding="utf-8")
    print(f"  ok  {a_out.name}  ({len(text)//1000}k chars)")
    return True


if __name__ == "__main__":
    ok, failed = 0, 0
    for doc_type, items in SOURCES.items():
        a_dir = CORPUS / doc_type
        a_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n{doc_type}:")
        for name, as_url in items:
            a_path = a_dir / f"{name}.pdf"
            if grab(as_url, a_path):
                ok += 1
                to_text(a_path)
            else:
                failed += 1
            time.sleep(1)

    print("\nloan, RBI circular:")
    if grab_rbi():
        ok += 1
    else:
        failed += 1

    print("\nrental, Model Tenancy Act 2021:")
    if grab_mta():
        ok += 1
    else:
        failed += 1

    print(f"\n{ok} downloaded, {failed} failed")
    if failed:
        print("Rules citing anything above that failed are withheld from reports,")
        print("not shown ungrounded. Check with: python tools/evaluate.py --groundedness rental")
    if failed:
        sys.exit(1)
