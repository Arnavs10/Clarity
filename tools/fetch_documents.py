"""
Pulls real employment agreements from SEC EDGAR. No account, no API key, no asking
anyone for anything.

    python tools/fetch_documents.py            # default, 25 documents
    python tools/fetch_documents.py --n 40

EDGAR full-text search is a free public JSON endpoint at efts.sec.gov. Public
companies file executive employment agreements as EX-10 exhibits, so the full text
of thousands of them is sitting there in the open. These are real, negotiated
contracts, not templates, which is exactly what template sources cannot give you.

SEC asks for a real contact address in the User-Agent and for requests to stay under
10 a second. Both are handled below. Do not remove the sleep.
"""

import argparse
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "raw"

# SEC requires a real contact. Swap this for your own address if you fork the repo.
CONTACT = "Arnav Shukla arnavshuklaforbusiness@gmail.com"

EFTS = "https://efts.sec.gov/LATEST/search-index"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data"

# Several angles per type, because one query returns the same handful of companies.
QUERIES = {
    "employment": [
        '"employment agreement"',
        '"executive employment agreement"',
        '"non-competition agreement"',
        '"confidentiality and non-solicitation"',
        '"offer of employment"',
    ],
    "rental": [
        '"lease agreement"',
        '"the tenant shall"',
        '"security deposit" "landlord"',
        '"premises" "monthly rent"',
        '"sublease agreement"',
    ],
    "loan": [
        '"loan agreement"',
        '"credit agreement"',
        '"the borrower shall"',
        '"promissory note"',
        '"events of default"',
    ],
}

HEADERS = {
    "User-Agent": CONTACT,
    "Accept": "application/json",
    "Accept-Encoding": "gzip, deflate",
}

MIN_CHARS = 4000       # anything shorter is a cover page, not an agreement
MAX_CHARS = 120000     # anything longer is a whole 10-K that slipped through


def get(as_url, as_headers=None, as_json=False):
    req = urllib.request.Request(as_url, headers=as_headers or HEADERS)
    with urllib.request.urlopen(req, timeout=45) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            import gzip
            raw = gzip.decompress(raw)
    text = raw.decode("utf-8", errors="ignore")
    return json.loads(text) if as_json else text


def search(as_query, start=0):
    """One page of EDGAR full text search. Returns the raw hit list."""
    params = {"q": as_query, "forms": "8-K,10-K,10-Q,S-1", "from": start}
    as_url = f"{EFTS}?{urllib.parse.urlencode(params)}"
    try:
        data = get(as_url, as_json=True)
        return data.get("hits", {}).get("hits", [])
    except Exception as e:
        print(f"  search failed for {as_query}: {e}")
        return []


def hit_to_url(hit):
    """
    EDGAR ids look like  0001104659-21-080527:tm2119438d1_ex10-1.htm
    The archive path wants the accession number with the dashes stripped.
    """
    a_id = hit.get("_id", "")
    if ":" not in a_id:
        return None, None

    accession, filename = a_id.split(":", 1)
    ciks = hit.get("_source", {}).get("ciks", [])
    if not ciks:
        return None, None

    cik = str(int(ciks[0]))          # strip the zero padding
    acc = accession.replace("-", "")
    return f"{ARCHIVE}/{cik}/{acc}/{filename}", hit.get("_source", {}).get("file_type", "")


BLOCK_TAGS = ["p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "blockquote"]


def repair_lines(as_text):
    """
    Safety net for text that arrives already fragmented.

    Rejoins a line onto the one before it unless there is a real boundary: a blank
    line, or a previous line that finished a sentence followed by something that
    looks like a new one.
    """
    out, buf = [], ""

    for raw in as_text.split("\n"):
        line = raw.strip()

        if not line:
            if buf:
                out.append(buf)
                buf = ""
            out.append("")
            continue

        if not buf:
            buf = line
            continue

        ended = buf.endswith((".", ":", ";", "?", "!"))
        fresh = bool(re.match(r"^(?:\(?[a-zA-Z0-9]{1,4}[.)]|[A-Z]{2,}|ARTICLE|Section)", line))

        if ended and fresh:
            out.append(buf)
            buf = line
        elif buf.endswith("-"):
            buf += line                      # hyphen carried over a break
        else:
            buf += " " + line

    if buf:
        out.append(buf)

    return re.sub(r"\n{3,}", "\n\n", "\n".join(out))


def strip_html(as_html):
    """
    SEC exhibits wrap individual letters in font and span tags for styling. Calling
    get_text with a newline separator therefore breaks words apart mid-letter, which
    is how "GROSS-UP FOR TAXES" turns into seven lines. Only block level tags should
    produce a line break, so those are marked first and inline tags are joined with
    nothing at all.
    """
    try:
        from bs4 import BeautifulSoup, NavigableString
        soup = BeautifulSoup(as_html, "html.parser")

        for tag in soup(["script", "style", "table"]):
            tag.decompose()
        for br in soup.find_all("br"):
            br.replace_with(NavigableString("\n"))
        for tag in soup.find_all(BLOCK_TAGS):
            tag.append(NavigableString("\n"))

        text = soup.get_text("")
    except ImportError:
        text = re.sub(r"<(?:p|div|tr|li|h[1-6]|br)[^>]*>", "\n", as_html, flags=re.I)
        text = re.sub(r"<[^>]+>", "", text)

    text = re.sub(r"&nbsp;?", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&#\d+;", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return repair_lines(text.strip())


def looks_like_an_agreement(as_text):
    """Cheap sanity filter. Cover letters and press releases do not survive this."""
    if not (MIN_CHARS <= len(as_text) <= MAX_CHARS):
        return False
    low = as_text[:6000].lower()
    signals = ["agreement", "employee", "employment", "shall"]
    return sum(1 for s in signals if s in low) >= 3


def run(doc_type, n_wanted):
    OUT.mkdir(parents=True, exist_ok=True)
    seen = set()
    saved = 0

    as_queries = QUERIES[doc_type]
    print(f"pulling up to {n_wanted} {doc_type} documents from EDGAR\n")

    for as_query in as_queries:
        if saved >= n_wanted:
            break
        print(f"query: {as_query}")

        for page in (0, 10, 20):
            if saved >= n_wanted:
                break

            hits = search(as_query, start=page)
            time.sleep(0.5)
            if not hits:
                break

            for hit in hits:
                if saved >= n_wanted:
                    break

                as_url, file_type = hit_to_url(hit)
                if not as_url or as_url in seen:
                    continue
                seen.add(as_url)

                # EX-10 is the material contracts exhibit. That is where these live.
                if not file_type.upper().startswith("EX-10"):
                    continue

                try:
                    html = get(as_url, as_headers={"User-Agent": CONTACT})
                    time.sleep(0.3)
                except Exception as e:
                    print(f"  skip ({e})")
                    continue

                text = strip_html(html)
                if not looks_like_an_agreement(text):
                    continue

                saved += 1
                a_path = OUT / f"{doc_type}_{saved:02d}.txt"
                a_path.write_text(text, encoding="utf-8")
                print(f"  [{saved:>2}] {a_path.name}  {len(text)//1000}k chars  {file_type}")

    print(f"\nsaved {saved} {doc_type} documents to {OUT}")
    if saved:
        print("\nnext:")
        print(f"  python src/segment.py --all {doc_type}")
    else:
        print("\nnothing came back. Check your connection, then see docs/SOURCES.md")
        print("for the CUAD fallback, which is a straight download with no API involved.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--type", default="employment", choices=sorted(QUERIES.keys()),
                    help="which document type to pull")
    ap.add_argument("--n", type=int, default=20, help="how many documents to save")
    a_args = ap.parse_args()
    run(a_args.type, a_args.n)
