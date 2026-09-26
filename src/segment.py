"""
Clause segmenter. Replaces bootstrap_split.py.

Real contracts number themselves half a dozen different ways, sometimes two ways in
the same document. Rather than guessing, this tries every pattern it knows, scores
each split by how many blocks land in a sensible size range, and keeps the winner.
Then it fixes the two failure modes that ruin labeling: blocks that are far too big
to carry one category, and fragments too small to be a clause at all.

Clause ids stay content-hashed, so anything already labeled keeps its label.

    python src/segment.py data/raw/employment_04.txt employment
    python src/segment.py --all employment
"""

import argparse
import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from schema import Clause, make_clause_id, write_jsonl   # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
OUT_DIR = ROOT / "data" / "clauses"

# A clause can be short and still be the worst one in the document. "The entire
# security deposit shall stand forfeited." is forty characters. Merging at 120 folded
# clauses like that into whichever block preceded them, where they could then be
# classified by their neighbour rather than on their own terms.
MIN_CHARS = 55        # below this it is a fragment, merge it back
# Clauses in an offer letter or a short rent agreement routinely run to a hundred
# characters. A floor of 200 meant a correct three-way split of such a document
# scored zero while doing nothing scored one, so the winner was the pattern that
# left the page in one piece.
GOOD_MIN = 80         # the range a real clause usually lands in
GOOD_MAX = 3000
MAX_CHARS = 4500      # above this, split it further or drop it

# Tried in order. Each entry is (name, regex, max_heading_line_length).
#
# The length cap only makes sense for the loose patterns. Once inline HTML tags are
# joined properly a heading shares its line with the clause body, so "7.4 GROSS-UP
# FOR TAXES. The Executive shall..." arrives as one 700 character line. Capping that
# at 140 silently merged every clause into one, so the numbered patterns take no cap
# at all: their regexes are anchored and specific enough to stand alone.
PATTERNS = [
    # "01 POSITION SNAPSHOT", "05 CONFIDENTIALITY AND INTELLECTUAL PROPERTY".
    # Indian offer letters and rent agreements number sections this way constantly:
    # a zero-padded number, a space, then the title in caps, with no punctuation
    # after the number. Every other pattern here expects "5." or "5)", so a real
    # offer letter fell through to the caps pattern, which then matched the company
    # letterhead instead and split the document by page.
    ("numbered_caps",
     re.compile(r"^\s*\d{1,2}\s+[A-Z][A-Z0-9 ,&'\-/()]{4,70}\s*$"), 90),
    ("article",
     re.compile(r"^\s*(?:ARTICLE|Article|SECTION|Section)\s+(?:[IVXLCDM]+|\d+)\b.*$"), None),
    ("dotted",
     re.compile(r"^\s*\d+\.\d+(?:\.\d+)?[.)]?\s+\S.*$"), None),
    ("numbered",
     re.compile(r"^\s*\d{1,2}[.)]\s+\S.*$"), None),
    ("roman",
     re.compile(r"^\s*[IVXLCDM]{1,6}[.)]\s+\S.*$"), None),
    ("lettered",
     re.compile(r"^\s*\([a-zA-Z]\)\s+\S.*$"), None),
    # A short standalone line with no terminal punctuation, in title case. Indian
    # appointment letters routinely head sections with prose like "In plain language"
    # or "Your first ninety days" rather than a number or block capitals. Nothing
    # here matched those, so a multi page letter came back as two clauses and most of
    # it was never examined as its own clause.
    # A trailing colon is allowed. Indian offer letters head sections "Probation:",
    # "Period of Service:", "NOTICE PERIOD:" far more often than not, and without the
    # colon a four page letter came back as four clauses with its notice period and
    # service commitment never read.
    ("prose_heading",
     re.compile(r"^\s*[A-Z][A-Za-z][A-Za-z ,'&/()-]{4,60}:?\s*$"), 60),
    ("caps",
     re.compile(r"^\s*[A-Z][A-Z0-9 ,&'\-/()]{6,70}[.:!]?\s*$"), 90),
]


# Contact details, identifiers and page markers. A line carrying one of these and
# repeating is furniture, whatever its length.
FURNITURE = re.compile(
    r"(@|www\.|http|\+\d{2}[\s-]|\bCIN\b|\bGST\b|\bpage\s+\d|\bpvt\.?\s*ltd\b"
    r"|\bprivate\s+limited\b|\bcorporate\s+office\b|\bregd\.?\s*office\b)",
    re.IGNORECASE)


# A page separator that cannot occur in a contract. read_text keeps page
# boundaries so the positional strip below has something to work with, then
# removes them before anything else sees the text.
PAGE_BREAK = "\x0c<<<CLARITY-PAGE>>>\x0c"


# An explicit page marker. A clause heading does not announce which page it is on,
# so a line in the top or bottom band carrying one of these is furniture on its own
# evidence, without needing to repeat. The generated agreements put the section
# banner and the page number on the same line, "Page 2 TENURE, DEFAULT AND EXIT",
# which made the banner unique per page and invisible to any repeat test.
AS_PAGE_MARK = re.compile(r"(\bpage\s+\d|\bp\.\s*\d+\b|\b\d+\s+of\s+\d+\b)", re.I)


def _digits_out(a_line):
    """
    A line with every run of digits replaced, for comparing lines that differ
    only by their page number. "Page 3 of 9" and "Page 4 of 9" are the same
    footer and have to hash the same or neither is ever recognised as furniture.
    """
    return re.sub(r"\d+", "#", a_line)


def strip_page_furniture(as_pages, min_pages=2):
    """
    Drop running heads and feet using their position on the page.

    strip_repeating_lines works on text alone and cannot see a banner that changes
    on every page. A real generated agreement carried "Page 2 TENURE, DEFAULT AND
    EXIT TERM AND TERMINATION" across the top of each page. The text differed every
    time, so it was never a repeat, and it landed inside the last clause of the
    preceding page. Segmenting on a heading that is really a page header is how a
    clause gets swallowed: the loan agreement's clause 13 was absorbed into clause
    12.4 and never examined by anything.

    A line in the top or bottom band of a page is furniture when either

      it repeats across pages once its digits are masked, so "1 Highly
      Confidential" and "4 Highly Confidential" are recognised as one footer, or

      it carries an explicit page marker, which a clause heading does not.

    Blank lines are ignored when working out the band, because a PDF that extracts
    with a leading empty line would otherwise push the real header out of range and
    the whole check would quietly do nothing.
    """
    if len(as_pages) < min_pages:
        return as_pages

    a_band = 2

    def edges(a_page):
        """(original index, normalised text) for the band lines of one page."""
        as_rows = [(i, re.sub(r"\s+", " ", ln).strip())
                   for i, ln in enumerate(a_page.split("\n"))
                   if ln.strip()]
        return as_rows[:a_band] + as_rows[-a_band:]

    as_slots = {}
    for a_page in as_pages:
        for _, a_plain in set(edges(a_page)):
            a_key = _digits_out(a_plain)
            as_slots[a_key] = as_slots.get(a_key, 0) + 1

    as_drop = {k for k, n in as_slots.items()
               if n >= min_pages and (FURNITURE.search(k) or len(k) <= 120)}

    out = []
    for a_page in as_pages:
        as_band_idx = {i for i, _ in edges(a_page)}
        as_lines = a_page.split("\n")
        a_keep = []
        for i, ln in enumerate(as_lines):
            if i in as_band_idx:
                a_plain = re.sub(r"\s+", " ", ln).strip()
                # The page-marker test runs on the real text. Running it on the
                # masked text was a silent no-op: masking turns "Page 2" into
                # "Page #", which the marker pattern cannot match, so the banners
                # this check exists for were never removed.
                if _digits_out(a_plain) in as_drop or AS_PAGE_MARK.search(a_plain):
                    continue
            a_keep.append(ln)
        out.append("\n".join(a_keep))
    return out


# A document's own table of contents, repeated as a banner at the top of a section:
#
#     PRICING AND SERVICING
#     INTEREST, FEES AND PAYMENT ADMINISTRATION
#     Clauses 4-10 | Rate, fees, prepayment, statements and electronic mandates
#
# The navigation line is the anchor. Nothing in an operative clause reads "Clauses
# 4-10 |", so it can be removed on sight, and the block-capital title lines sitting
# directly above it go with it. The title lines are never removed on their own,
# because a line in capitals is often a real heading and removing one merges its
# clause into the one before it, which is the exact failure this is meant to stop.
AS_NAV_LINE = re.compile(
    r"^\s*(?:clauses?|sections?|articles?)\s+\d{1,3}\s*(?:-|\u2013|\u2014|to)\s*\d{1,3}"
    r"\s*(?:\||:|$)", re.I)
AS_CAPS_TITLE = re.compile(r"^\s*[A-Z][A-Z0-9 ,&'/()\-]{2,70}\s*$")
AS_NUMBERED_START = re.compile(r"^\s*(?:\d{1,2}(?:\.\d{1,2})*[.)]?|\([a-z0-9]{1,3}\))\s")


def strip_section_banners(as_text):
    """
    Remove "Clauses 4-10 | ..." navigation banners and the capital titles above them.

    On the real generated loan and rent agreements these banners landed inside the
    last clause of the preceding section on every page: clause 3 of the loan ended
    "standard servicing waterfall. PRICING AND SERVICING INTEREST, FEES AND PAYMENT
    ADMINISTRATION Clauses 4-10 | Rate, fees...". The page-number test cannot see
    them, because they carry no page number and differ on every page.
    """
    as_lines = as_text.split("\n")
    as_drop = set()
    for i, ln in enumerate(as_lines):
        if not AS_NAV_LINE.match(ln):
            continue
        as_drop.add(i)
        # Walk upwards over the banner's title lines, at most three, stopping at the
        # first line that is not a capital title or that starts like a clause.
        j = i - 1
        while j >= 0 and i - j <= 3:
            a_prev = as_lines[j]
            if not a_prev.strip():
                j -= 1
                continue
            if AS_NUMBERED_START.match(a_prev) or not AS_CAPS_TITLE.match(a_prev):
                break
            as_drop.add(j)
            j -= 1
    if not as_drop:
        return as_text
    return "\n".join(ln for i, ln in enumerate(as_lines) if i not in as_drop)


AS_FURNITURE_WORDS = re.compile(
    r"(?:confidential|\bpage\b|all\s+rights\s+reserved|\u00a9|copyright\s+\d{4}"
    r"|for\s+software\s+testing|not\s+a\s+real\s+contract)", re.I)


def _furniture_like(a_line):
    """Does this line say the kind of thing a running head or foot says."""
    return bool(FURNITURE.search(a_line) or AS_FURNITURE_WORDS.search(a_line))


def strip_repeating_lines(as_text, min_repeats=2):
    """
    Remove headers and footers that repeat on every page.

    A PDF letterhead appears once per page. Left in, an all-caps company name is
    indistinguishable from an all-caps section heading, so the document gets split
    by page and the actual clauses end up buried inside page-sized blocks. That is
    how a real offer letter came back as three clauses and no findings.

    A line is dropped when it repeats and either is short enough to be furniture or
    carries a contact detail, company identifier or page marker. Repetition alone is
    not enough, because a genuine clause can recur.

    The threshold was three repeats, which meant a two page contract kept its
    letterhead on every clause. Offer letters and rent agreements are very often two
    pages, so that was the common case rather than the edge one.
    """
    as_lines = as_text.split("\n")
    as_norm = [re.sub(r"\s+", " ", ln).strip() for ln in as_lines]
    # The page number is the part of a footer that changes, so matching on the
    # literal text made every footer unique and none of them ever reached the
    # repeat threshold. A real offer letter carried "1 Highly Confidential",
    # "4 Highly Confidential", "6 Highly Confidential" and so on, one per page,
    # and every one of them survived into the middle of a clause.
    # Digits are masked ONLY on lines that look like furniture.
    #
    # v37 masked every line, so "1 Highly Confidential" and "4 Highly Confidential"
    # were recognised as one footer. But it also made "Late fee Rs. 500" and "Late fee
    # Rs. 750" the same line, and deleted both, and would have deleted an entire EMI
    # schedule the same way. A fee table is contract, not furniture. A line is only
    # compared with its numbers masked when it says what furniture says: a page
    # marker, a confidentiality stamp, or one of the letterhead markers in FURNITURE.
    as_key = [_digits_out(ln) if _furniture_like(ln) else ln for ln in as_norm]

    as_counts = {}
    for i, ln in enumerate(as_norm):
        if len(ln) < 6:
            continue
        a_short = len(ln) <= 120 and len(ln.split()) <= 16
        if a_short or FURNITURE.search(ln):
            as_counts[as_key[i]] = as_counts.get(as_key[i], 0) + 1

    as_drop = {k for k, n in as_counts.items() if n >= min_repeats}
    if not as_drop:
        return as_text

    as_kept = [orig for orig, k in zip(as_lines, as_key) if k not in as_drop]
    return "\n".join(as_kept)


def read_bytes(as_bytes, a_suffix):
    """
    Read a document that is already in memory.

    The API used to write each upload to a temporary file so it could be read back
    by path. The file was deleted afterwards, but the page says an upload is never
    written to disk, and while that file existed the sentence was false. pdfplumber
    and python-docx both read from a stream, so nothing needs the file.
    """
    sfx = (a_suffix or "").lower()
    if sfx and not sfx.startswith("."):
        sfx = "." + sfx

    if sfx in (".txt", ".text"):
        # Same line endings a file read in text mode would give.
        a_txt = as_bytes.decode("utf-8", errors="ignore")
        return a_txt.replace("\r\n", "\n").replace("\r", "\n")
    if sfx == ".pdf":
        try:
            import pdfplumber
        except ImportError:
            raise RuntimeError(
                "PDF support needs pdfplumber, which is not installed in the Python "
                "running this server. Activate the project virtualenv and restart, or "
                "install it: pip install pdfplumber. Text and DOCX files still work."
            ) from None
        with pdfplumber.open(io.BytesIO(as_bytes)) as pdf:
            as_pages = [p.extract_text() or "" for p in pdf.pages]
        return "\n".join(strip_page_furniture(as_pages))
    if sfx == ".docx":
        import docx
        return "\n".join(p.text for p in docx.Document(io.BytesIO(as_bytes)).paragraphs)
    raise ValueError(f"unsupported file type: {sfx}")


def read_text(a_path):
    a_path = Path(a_path)
    return read_bytes(a_path.read_bytes(), a_path.suffix)


AS_DOTTED_HEAD = re.compile(r"^\s*(\d{1,2})\.\d{1,2}(?:\.\d{1,2})?[.)]?\s+\S")
AS_PLAIN_HEAD = re.compile(r"^\s*(\d{1,2})[.)]\s+(\S.*?)\s*$")


def split_dotted_mixed(as_lines):
    """
    Split on "12.4." sub-clauses AND on "13." sections that have no sub-clauses.

    A document numbered "12.4." for most clauses and plain "13." for short ones was
    split on the dotted form only, so every section without sub-clauses vanished into
    the clause before it. On a real internship offer letter that was six clauses:
    Performance, Notices, Successors and Assigns, Severability, Dispute Resolution and
    Governing Law, the last carrying an exclusive-jurisdiction term no rule ever saw.

    A plain "N." line is only a section heading when all three hold:

      it is the NEXT section, one more than the section currently open,
      it reads as a short title rather than a sentence, and
      it does not end in a comma or semicolon.

    All three are needed. That same letter's harassment clause lists its definitions
    as "1. unwelcome sexual advances, ... 11. humiliating treatment", and a pattern
    that took every "N." line would have cut that one clause into eleven.
    """
    blocks, current, head = [], None, []
    a_major = 0
    for ln in as_lines:
        a_is_head = False
        m = AS_DOTTED_HEAD.match(ln)
        if m:
            a_major = int(m.group(1))
            a_is_head = True
        else:
            m = AS_PLAIN_HEAD.match(ln)
            if m:
                a_n, a_rest = int(m.group(1)), m.group(2)
                # With no section open yet, any short title may open one. The
                # splitter is also handed fragments cut from the middle of a document
                # by resplit_oversized, and a fragment starting "12. Termination" has
                # no section 11 before it, so a strict "one more than the last" test
                # threw away the heading of every fragment it was given.
                a_next = a_major == 0 or a_n == a_major + 1
                if (a_next and len(a_rest) <= 60
                        and not a_rest.endswith((",", ";"))):
                    a_major = a_n
                    a_is_head = True
        if a_is_head:
            if current:
                blocks.append(current)
            current = [ln]
        elif current is not None:
            current.append(ln)
        else:
            head.append(ln)
    if current:
        blocks.append(current)

    # A section heading with no text of its own ("12. Termination" followed at once
    # by "12.1. This letter shall expire") belongs to the clause that follows it. The
    # general tidy step merges short fragments backwards, which left "12. Termination"
    # dangling off the end of the indemnity clause and "7. Ownership of Intellectual
    # Property" off the end of the confidentiality one. Carry it forward instead, so
    # the heading sits over the text it actually heads.
    as_merged, as_carry = [], []
    for b in blocks:
        as_body_lines = [ln for ln in b[1:] if ln.strip()]
        if not as_body_lines and len(b[0].strip()) <= 70:
            as_carry.append(b[0])
            continue
        as_merged.append(as_carry + b)
        as_carry = []
    if as_carry:
        if as_merged:
            as_merged[-1].extend(as_carry)
        else:
            as_merged.append(as_carry)

    out = []
    a_h = re.sub(r"\n{3,}", "\n\n", "\n".join(head).strip())
    if a_h:
        out.append(a_h)
    for b in as_merged:
        a_body = re.sub(r"\n{3,}", "\n\n", "\n".join(b).strip())
        if a_body:
            out.append(a_body)
    return out


def split_named(a_name, as_lines, a_pattern, a_cap):
    """The dotted pattern also honours plain section numbers. Every other is unchanged."""
    if a_name == "dotted":
        return split_dotted_mixed(as_lines)
    return split_on(as_lines, a_pattern, a_cap)


def split_on(as_lines, a_pattern, max_len=None, keep_head=True):
    """
    Cut the line list wherever the pattern matches.

    Text before the first match is kept as its own block by default. Dropping it is
    how a split quietly loses half a document: the head of an oversized section is
    usually the section itself, and everything after it is only its sub-clauses.
    """
    blocks, current, head = [], None, []

    for line in as_lines:
        if a_pattern.match(line) and (max_len is None or len(line.strip()) < max_len):
            if current:
                blocks.append(current)
            current = [line]
        elif current is not None:
            current.append(line)
        else:
            head.append(line)

    if current:
        blocks.append(current)

    out = []
    if keep_head and head:
        h = re.sub(r"\n{3,}", "\n\n", "\n".join(head).strip())
        if h:
            out.append(h)

    for b in blocks:
        body = re.sub(r"\n{3,}", "\n\n", "\n".join(b).strip())
        if body:
            out.append(body)
    return out


def hard_cut(as_text, target=GOOD_MAX):
    """
    Last resort for a block no pattern will divide. Cut on sentence boundaries near
    the target size. Ugly, but a labelable chunk beats a discarded one.
    """
    as_sentences = re.split(r"(?<=[.;])\s+", as_text)
    out, buf = [], ""

    for s in as_sentences:
        if len(buf) + len(s) > target and buf:
            out.append(buf.strip())
            buf = s
        else:
            buf = f"{buf} {s}".strip()

    if buf.strip():
        out.append(buf.strip())
    return out or [as_text]


def score(as_blocks, total_chars):
    """
    How good is this split. Two things matter, and coverage matters more.

    A pattern can produce beautifully sized blocks while quietly throwing away most
    of the document, because everything before its first match gets dropped. That is
    worse than a clumsy split, so coverage gates the score rather than adding to it.
    """
    if not as_blocks or total_chars == 0:
        return -1

    kept = sum(len(b) for b in as_blocks)
    coverage = kept / total_chars

    if coverage < 0.45:
        return -1

    good = sum(1 for b in as_blocks if GOOD_MIN <= len(b) <= GOOD_MAX)
    huge = sum(1 for b in as_blocks if len(b) > MAX_CHARS)

    # A small per-block bonus. Splitting is the job, so where two patterns retain the
    # same content and produce equally sensible sizes, the one that actually divided
    # the document should win. Kept small so it only breaks near ties.
    return (good - huge * 2 + 0.15 * len(as_blocks)) * coverage


def split_paragraphs(as_text):
    """
    Blank-line split. The floor under everything else.

    When no heading pattern fits, the alternative to this is handing the rules one
    block containing the whole document, where a single risky sentence sits among
    twenty harmless ones and is read as part of them.
    """
    as_out, as_cur = [], []
    for line in as_text.split("\n"):
        if line.strip():
            as_cur.append(line)
        elif as_cur:
            as_out.append("\n".join(as_cur))
            as_cur = []
    if as_cur:
        as_out.append("\n".join(as_cur))
    return as_out


def best_split(as_text):
    """
    Try every pattern through the full pipeline, keep whichever survives best.

    Scoring after resplit and tidy rather than before, because a pattern that leaves
    one enormous block may still win once that block is cut down.
    """
    as_lines = as_text.split("\n")
    total = len(as_text)
    best, best_score, best_name = [], -1, "none"

    for name, pat, cap in PATTERNS:
        blocks = tidy(resplit_oversized(split_named(name, as_lines, pat, cap)))
        s = score(blocks, total)
        if s > best_score:
            best, best_score, best_name = blocks, s, name

    # nothing numbered survived, fall back to blank-line paragraphs
    if best_score <= 0:
        paras = [p.strip() for p in re.split(r"\n\s*\n", as_text) if p.strip()]
        paras = tidy(resplit_oversized(paras))
        if score(paras, total) > best_score:
            best, best_name = paras, "paragraph"

    return best, best_name


def resplit_oversized(as_blocks):
    """A block over MAX_CHARS holds several clauses. Cut it on the next pattern down."""
    out = []
    for b in as_blocks:
        if len(b) <= MAX_CHARS:
            out.append(b)
            continue

        pieces = None
        for name, pat, cap in PATTERNS[1:]:             # skip article, too coarse
            cand = split_named(name, b.split("\n"), pat, cap)
            if len(cand) > 1 and max(len(c) for c in cand) < len(b):
                pieces = cand
                break

        if pieces is None:
            pieces = [p.strip() for p in re.split(r"\n\s*\n", b) if p.strip()]

        if len(pieces) <= 1 or max(len(p) for p in pieces) > MAX_CHARS:
            pieces = [q for p in pieces for q in (hard_cut(p) if len(p) > MAX_CHARS else [p])]

        out.extend(pieces)
    return out


# Words that make a block operative: it grants a right, imposes a duty, or sets a
# term. A block with none of these is addressing, dating or signing, not agreeing.
AS_OBLIGATION = re.compile(
    r"\b(shall|must|will|may|agree[sd]?|undertake[sn]?|entitled|required|liable"
    r"|responsible|obliged|covenant|warrant|represent|acknowledge[sd]?|consent"
    r"|payable|paid|pay|reimburse|indemnif\w+|terminat\w+|notice|period|deposit"
    r"|rent|interest|salary|stipend|ctc|compensation|confidential|assign\w*"
    r"|subject\s+to|governed\s+by|in\s+accordance|not\s+negotiable)\b", re.I)

# The furniture every letter has: who it is to, when it was written, who signed it,
# and where the company is registered.
AS_LETTER_FURNITURE = re.compile(
    r"^\s*(date\s*[:\-]|dear\b|to\s*[:,]|from\s*[:,]|subject\s*[:\-]|re\s*[:\-]"
    r"|sincerely|yours\s+(faithfully|sincerely|truly)|regards|thanking\s+you"
    r"|for\s+[A-Z][\w& .]+(pvt|private|ltd|limited|llp)"
    r"|registered\s+office|corporate\s+office|cin\s*[:\-]|encl\w*\s*[:\-]"
    r"|signature\s+of|authorised\s+signator|welcome\s+to\s+the"
    r"|(director|manager|vice\s+president|president|partner|proprietor)\s*$"
    r"|[A-Z][A-Z& .]{6,}(pvt|private|ltd|limited|llp)\b)", re.I)


def is_operative(as_block):
    """
    Does this block actually say something a party is bound by?

    A letter is not all contract. "Date: 3 September 2026. Dear [Candidate],
    [University]" was being listed and counted as a clause of the agreement,
    which is wrong twice over: it inflates the number of clauses a reader is told
    were checked, and it puts rules to work on an address.

    The test is narrow on purpose. A block is dropped only when it OPENS with
    letter furniture and contains nothing that creates an obligation. An earlier
    version also dropped anything short without internal punctuation, and that ate
    "01 POSITION You are offered the role of Data Analyst reporting to the Head of
    Analytics", which is a real term of the offer. A filter that removes real
    clauses is worse than the problem it was written for, so the heuristic is gone
    and only the explicit opener test remains.
    """
    a_text = as_block.strip()
    if len(a_text) > 400:
        return True
    if AS_OBLIGATION.search(a_text):
        return True

    # Check the first two non-empty lines: a footer often opens with a bare role
    # ("DIRECTOR") and puts the address on the line below.
    as_lines = [ln.strip() for ln in a_text.split("\n") if ln.strip()][:2]
    return not any(AS_LETTER_FURNITURE.match(ln) for ln in as_lines)


def tidy(as_blocks):
    """Merge fragments into the block before them, then drop what is still unusable."""
    merged = []
    for b in as_blocks:
        if merged and len(b) < MIN_CHARS:
            merged[-1] = merged[-1] + "\n" + b
        else:
            merged.append(b)

    a_sized = [b for b in merged if MIN_CHARS <= len(b) <= MAX_CHARS]

    # Letter furniture is not a clause. Keep everything if the filter would empty
    # the document, because a wrong filter must never turn a contract into nothing.
    a_kept = [b for b in a_sized if is_operative(b)]
    return a_kept if a_kept else a_sized


def first_line(as_text):
    for line in as_text.split("\n"):
        if line.strip():
            return line.strip()[:120]
    return None


def split_text(raw, doc_type, doc_id="upload"):
    """
    Segment text that is already in memory. Writes nothing.

    Returns (clauses, how, chars_stripped). This is the whole of what run() did
    between reading the file and saving the result, so a document splits the same
    way whether it arrives as a path or as text.
    """
    # A file read in text mode has its line endings normalised. Text that never
    # went through a file has not, so normalise here too.
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")

    a_before = len(raw)
    raw = strip_repeating_lines(raw)
    raw = strip_section_banners(raw)
    n_stripped = a_before - len(raw)

    blocks, how = best_split(raw)

    as_clauses = []
    cursor = 0
    for i, body in enumerate(blocks or []):
        start = raw.find(body[:60], cursor)
        if start < 0:
            start = cursor
        cursor = start + len(body)

        as_clauses.append(
            Clause(
                clause_id=make_clause_id(doc_id, i, body),
                doc_id=doc_id,
                doc_type=doc_type,
                text=body,
                order=i,
                heading=first_line(body),
                char_start=start,
                char_end=cursor,
            )
        )
    return as_clauses, how, n_stripped


def run(a_path, doc_type, quiet=False, save=False):
    a_path = Path(a_path)
    as_doc_id = a_path.stem
    raw = read_text(a_path)

    as_clauses, how, n_stripped = split_text(raw, doc_type, as_doc_id)

    if not as_clauses:
        if not quiet:
            print(f"  {a_path.name:<26} nothing usable")
        return []

    # Saving is for building the corpus, and only the corpus builder asks for it.
    # It used to happen on every call, so every contract audited through the API or
    # the Streamlit app left a full copy of itself in data/clauses: a folder that is
    # not gitignored, and that the router and the labelling queue both train on.
    if save:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        write_jsonl(OUT_DIR / f"{as_doc_id}.jsonl", as_clauses)

    if not quiet:
        avg = sum(len(c.text) for c in as_clauses) // len(as_clauses)
        a_note = f"   (stripped {n_stripped} chars of page furniture)" if n_stripped > 200 else ""
        print(f"  {a_path.name:<26} {len(as_clauses):>3} clauses   avg {avg:>4} chars   "
              f"via {how}{a_note}")
    return as_clauses


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", help="one file, or use --all")
    ap.add_argument("doc_type")
    ap.add_argument("--all", action="store_true", help="every data/raw/<doc_type>_*.txt")
    ap.add_argument("--save", action="store_true",
                    help="one file only: also write it to data/clauses (--all always does)")
    a_args = ap.parse_args()

    if a_args.all:
        as_files = sorted(RAW_DIR.glob(f"{a_args.doc_type}_*.txt"))
        if not as_files:
            print(f"no files matching data/raw/{a_args.doc_type}_*.txt")
            sys.exit(1)

        total = 0
        print(f"segmenting {len(as_files)} {a_args.doc_type} documents\n")
        for f in as_files:
            total += len(run(f, a_args.doc_type, save=True))
        print(f"\n{total} clauses total for {a_args.doc_type}")
    else:
        if not a_args.path:
            print("give a file path, or use --all")
            sys.exit(1)
        # One file is usually a document being debugged, often a real one, so it is
        # not added to the corpus unless asked.
        run(a_args.path, a_args.doc_type, save=a_args.save)
