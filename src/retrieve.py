"""
Statute retrieval.

A rule says "section 27 Indian Contract Act". This finds the actual passage in the
bare Act sitting in data/corpus/ and hands back the verbatim text, so a finding can
show the reader the words it relied on instead of asking them to trust a summary.

Two lookups, and they answer different questions:

  by_section   the rule already names a section, so go straight to it. Exact, and
               it is what statutory and policy rules use.
  by_query     free text search over the corpus, for when a clause raises something
               no rule anticipated. BM25 over sentence windows.

BM25 is implemented here rather than pulled in, partly to keep the dependency list
short and partly because the scoring is worth being able to read. A dense retriever
can be added behind the same interface later without touching callers.
"""

import json
import math
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = ROOT / "data" / "corpus"
INDEX_PATH = ROOT / "data" / "corpus_index.json"

WINDOW_SENTENCES = 4
STRIDE = 2
K1, B = 1.5, 0.75

# The optional \d+\[ prefix carries an amendment marker. India Code prints a section
# that was substituted by a later Act as 1[14. Contracts not specifically enforceable,
# and without allowing for it the pattern skipped the real provision entirely and
# matched only the section's one-line entry in the Arrangement of Sections.
SECTION_RE = re.compile(
    r"^\s*(?:\d+\[\s*)?(?:section\s+)?(\d+[A-Z]{0,2})\s*[.．:\-–—]\s*(?=\S)",
    re.IGNORECASE | re.MULTILINE,
)


def tokenize(as_text):
    return re.findall(r"[a-z0-9]+", as_text.lower())


def sentences(as_text):
    as_text = re.sub(r"\s+", " ", as_text)
    return [s.strip() for s in re.split(r"(?<=[.;])\s+", as_text) if s.strip()]


def windows(as_text, doc_id, source_name):
    """Overlapping sentence windows, so a passage is never cut mid-thought."""
    as_sents = sentences(as_text)
    out = []
    for i in range(0, max(len(as_sents), 1), STRIDE):
        chunk = " ".join(as_sents[i:i + WINDOW_SENTENCES]).strip()
        if len(chunk) < 80:
            continue
        out.append({"corpus_id": doc_id, "source": source_name,
                    "text": chunk, "offset": i})
        if i + WINDOW_SENTENCES >= len(as_sents):
            break
    return out


def _anchors_in(as_text, as_anchors):
    """
    Every significant word of at least one anchor is present in the span.

    Deliberately the same rule as agent._anchor_present. It is duplicated rather
    than imported because agent imports retrieve, and a cycle here would break the
    API at startup rather than at the point of use.
    """
    a_hay = re.sub(r"[^a-z0-9 ]+", " ", (as_text or "").lower())
    a_hay = " ".join(a_hay.split())
    for a in as_anchors or []:
        as_words = [w for w in re.sub(r"[^a-z0-9 ]+", " ", a.lower()).split()
                    if len(w) > 2]
        if as_words and all(w in a_hay for w in as_words):
            return True
    return False


class StatuteIndex:
    def __init__(self, index_path=None):
        self.path = Path(index_path) if index_path else INDEX_PATH
        self.chunks = []
        self._df = Counter()
        self._avg_len = 1.0
        self._alias = self._build_alias()
        if self.path.exists():
            if self._is_stale():
                print("  (corpus changed since the index was built, rebuilding)")
                self.build()
            else:
                self._load()

    def _is_stale(self):
        """
        True when a corpus file is newer than the index, or when the set of files
        has changed.

        Adding a statute and reusing an old index means every rule citing it comes
        back with no span, the verify node rejects the finding, and the user sees a
        clean contract. Silence and safety must not look the same, so freshness is
        checked rather than assumed.
        """
        if not CORPUS_DIR.exists():
            return False

        as_files = sorted(CORPUS_DIR.rglob("*.txt"))
        if not as_files:
            return False

        a_index_mtime = self.path.stat().st_mtime
        if any(f.stat().st_mtime > a_index_mtime for f in as_files):
            return True

        try:
            as_indexed = {c["corpus_id"] for c in
                          json.loads(self.path.read_text())["chunks"]}
        except Exception:
            return True
        return {f.stem for f in as_files} != as_indexed

    # ---------- building ----------

    def build(self, as_registry=None):
        """Read every .txt in data/corpus/ and index it. Run once after fetch_corpus."""
        self.chunks = []
        if not CORPUS_DIR.exists():
            raise FileNotFoundError(
                f"no corpus at {CORPUS_DIR}. Run: python tools/fetch_corpus.py")

        as_files = sorted(CORPUS_DIR.rglob("*.txt"))
        if not as_files:
            raise FileNotFoundError(
                "corpus directory has no .txt files. fetch_corpus.py converts the "
                "downloaded PDFs; check that pdfplumber is installed.")

        for a_path in as_files:
            doc_id = a_path.stem
            raw = a_path.read_text(encoding="utf-8", errors="ignore")
            self.chunks.extend(windows(raw, doc_id, a_path.parent.name))

        self._fit()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"chunks": self.chunks}), encoding="utf-8")
        return len(self.chunks)

    def _fit(self):
        self._df = Counter()
        total = 0
        for c in self.chunks:
            as_toks = tokenize(c["text"])
            c["_len"] = len(as_toks)
            c["_tf"] = Counter(as_toks)
            total += len(as_toks)
            for t in set(as_toks):
                self._df[t] += 1
        self._avg_len = (total / len(self.chunks)) if self.chunks else 1.0

    def _load(self):
        self.chunks = json.loads(self.path.read_text())["chunks"]
        self._fit()

    # ---------- lookup ----------

    def by_section(self, corpus_id, section, context_chars=900):
        """
        The passage for one numbered section. Returns verbatim text, never a summary.

        Bare Acts number sections at the start of a line, so the span runs from that
        marker to the next one. If the number cannot be located the answer is None:
        a finding with no citation is better than a finding with a wrong one.

        A section number appears more than once in a real Act. India Code PDFs open
        with an Arrangement of Sections, a contents table listing every section by
        number and heading, so the first match for "74" is usually its one-line entry
        in that table and the span to the next entry is about seventy characters of
        heading. Returning the first match therefore returned the index rather than
        the law, for thirteen of nineteen grounded rules, and the finding was then
        withheld for having a heading-only citation. The verifier was right; the
        retrieval was wrong.

        So every occurrence is scored and the best one wins: the provision is the
        longest span, and a contents entry cannot compete with it.
        """
        as_paths = list(CORPUS_DIR.rglob(f"{corpus_id}.txt"))
        if not as_paths:
            return None

        raw = as_paths[0].read_text(encoding="utf-8", errors="ignore")
        as_marks = [(m.start(), m.group(1)) for m in SECTION_RE.finditer(raw)]

        as_best = None
        for i, (pos, num) in enumerate(as_marks):
            if num.upper() != str(section).upper():
                continue
            end = as_marks[i + 1][0] if i + 1 < len(as_marks) else len(raw)
            span = re.sub(r"\s+", " ", raw[pos:min(end, pos + context_chars)]).strip()
            if len(span) < 40:
                continue
            if as_best is None or len(span) > len(as_best[0]):
                as_best = (span, pos)

        if as_best is None:
            return None
        return {"corpus_id": corpus_id, "section": str(section),
                "text": as_best[0], "char_start": as_best[1], "exact": True}

    def by_query(self, as_query, k=3, corpus_ids=None):
        """BM25 over the sentence windows. Used when no rule named a section."""
        if not self.chunks:
            return []

        as_pool = [c for c in self.chunks
                   if not corpus_ids or c["corpus_id"] in corpus_ids]
        if not as_pool:
            return []

        n = len(self.chunks)
        as_terms = tokenize(as_query)
        scored = []

        for c in as_pool:
            score = 0.0
            for t in as_terms:
                tf = c["_tf"].get(t, 0)
                if not tf:
                    continue
                idf = math.log(1 + (n - self._df[t] + 0.5) / (self._df[t] + 0.5))
                denom = tf + K1 * (1 - B + B * c["_len"] / self._avg_len)
                score += idf * (tf * (K1 + 1)) / denom
            if score > 0:
                scored.append((score, c))

        scored.sort(key=lambda x: -x[0])
        return [{"corpus_id": c["corpus_id"], "source": c["source"],
                 "text": c["text"], "score": round(s, 3), "exact": False}
                for s, c in scored[:k]]

    def resolve_id(self, corpus_id):
        """
        Rules refer to a statute by a short id like ica_1872, while the corpus files
        are named after the Act. The type profiles already declare that mapping, so
        read it from there rather than duplicating it here.
        """
        if corpus_id in self._alias:
            return self._alias[corpus_id]
        if list(CORPUS_DIR.rglob(f"{corpus_id}.txt")):
            return corpus_id
        return None

    def _build_alias(self):
        as_map = {}
        try:
            from typeregistry import TypeRegistry
            as_reg = TypeRegistry()
            for slug in as_reg.slugs():
                for entry in as_reg.get(slug).get("corpus", []):
                    stem = Path(entry["path"]).stem
                    as_map[entry["id"]] = stem
        except Exception:
            pass
        return as_map

    # Windows tried in order when a rule declares anchors. by_section stops at the
    # next section marker regardless, so a wider window can never spill into the
    # following provision: it only stops cutting this one short.
    AS_WINDOWS = (900, 1800, 3200)

    def cite(self, as_source, anchors=None):
        """
        Resolve a rule's source block to a citation. Section first, query as fallback.

        When the rule declares anchor phrases, the window widens until the span
        actually contains them.

        Why: R-RENT-10 rests on section 17(1)(d) of the Registration Act, leases
        from year to year or for a term exceeding one year. That section runs 1779
        characters and clause (d) begins at 973. The default 900-character window
        stopped seventy-three characters short of it, so the citation shown to the
        reader covered gifts, non-testamentary instruments and receipts, and said
        nothing whatever about leases.

        Both of the other verification gates passed on that span. It opened at the
        right section and it was long enough. Only the anchor gate could catch it,
        and the rule had no anchor, which is how a citation that does not support
        its finding survived nineteen out of nineteen.

        A wider window is not a search for agreeable text. The span is still the
        named section, read verbatim, bounded by the next section marker. All this
        does is stop truncating a provision halfway through the sub-clause the rule
        depends on.
        """
        if not as_source:
            return None

        corpus_id = self.resolve_id(as_source.get("corpus_id")) or as_source.get("corpus_id")
        if as_source.get("section") and corpus_id:
            hit, a_prev_len = None, -1
            for a_win in self.AS_WINDOWS:
                a_try = self.by_section(corpus_id, as_source["section"],
                                        context_chars=a_win)
                if not a_try:
                    break
                hit = a_try
                if not anchors:
                    break
                if _anchors_in(hit.get("text", ""), anchors):
                    break
                # Stop when the span stops growing: that is the end of the section,
                # so widening again cannot help and the anchor is genuinely absent.
                #
                # Compared against the previous span, not against the requested
                # window. by_section normalises whitespace, so a full 900-character
                # request comes back as 899 and a test against the window reads
                # that as "the section ended here" on the very first pass, which
                # silently disabled the widening entirely.
                a_len = len(hit.get("text", ""))
                if a_len <= a_prev_len:
                    break
                a_prev_len = a_len
            if hit:
                return hit

        as_query = (as_source.get("query") or as_source.get("provision")
                    or as_source.get("act", ""))
        hits = self.by_query(as_query, k=1,
                             corpus_ids=[corpus_id] if corpus_id else None)
        return hits[0] if hits else None


if __name__ == "__main__":
    import sys
    idx = StatuteIndex()
    if not idx.chunks:
        print("building index...")
        print(f"  {idx.build()} chunks indexed")

    if len(sys.argv) > 2:
        print(json.dumps(idx.by_section(sys.argv[1], sys.argv[2]), indent=2)[:900])
    elif len(sys.argv) > 1:
        for h in idx.by_query(sys.argv[1]):
            print(f"\n[{h['score']}] {h['corpus_id']}\n  {h['text'][:260]}")
    else:
        print(f"{len(idx.chunks)} chunks across "
              f"{len(set(c['corpus_id'] for c in idx.chunks))} statutes")
