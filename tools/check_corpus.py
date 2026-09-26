#!/usr/bin/env python3
"""
Walk every grounded rule, retrieve the passage it cites, and say whether that
passage would survive verification.

This exists because "groundedness 42.9%" tells you a number and nothing else. Four
rules failed, and the four possible causes need four different fixes: the statute
file is missing, the section lookup found nothing, the span came back as a heading,
or the span does not contain the words the rule relies on. Reading the number alone,
all four look identical.

    python tools/check_corpus.py
    python tools/check_corpus.py rental
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rules import RuleEngine                    # noqa: E402
from retrieve import StatuteIndex               # noqa: E402
import agent as ag                              # noqa: E402

GROUNDED = ("statutory", "regulatory", "policy")


def check(doc_type, ix, engine):
    as_rules = [r for r in engine.rules_for(doc_type) if r["basis"] in GROUNDED]
    print(f"\n{'=' * 72}\n{doc_type.upper()}  {len(as_rules)} grounded rules\n{'=' * 72}")

    as_ok, as_bad = 0, []
    for r in as_rules:
        src = r.get("source") or {}
        a_cid, a_sec = src.get("corpus_id"), src.get("section")
        as_anchors = r.get("anchors") or []

        if not a_cid:
            as_bad.append((r["id"], "rule names no corpus_id"))
            continue

        # Go through cite(), which is what the agent itself calls. Calling
        # by_section directly skips resolve_id, so a rule citing ica_1872 looks for
        # a file literally named ica_1872.txt, finds nothing, and every rule reports
        # a missing statute while the corpus is sitting there intact.
        try:
            # Anchors go in, so this sees the same span the verifier will see.
            # Without them retrieval does not widen, and a rule whose provision
            # sits past the default window fails here while passing in the app,
            # or the reverse. The check has to exercise the real path.
            hit = ix.cite(src, anchors=r.get("anchors"))
        except Exception as e:
            as_bad.append((r["id"], f"lookup raised {type(e).__name__}: {e}"))
            continue

        if not hit or not (hit.get("text") or "").strip():
            a_resolved = ix.resolve_id(a_cid)
            as_bad.append((r["id"],
                f"no span retrieved for {a_cid} s.{a_sec}. "
                + (f"corpus_id resolves to '{a_resolved}.txt'. Is that file present?"
                   if a_resolved else
                   f"corpus_id '{a_cid}' resolves to nothing. Check the corpus block "
                   f"in config/types/.")))
            continue

        a_span = " ".join(hit["text"].split())

        if a_sec and str(a_sec) not in a_span[:120]:
            as_bad.append((r["id"], f"span does not open at section {a_sec}. "
                                    f"Opens with: {a_span[:70]}"))
            continue

        if len(a_span) < ag.MIN_SPAN_CHARS:
            as_bad.append((r["id"], f"span is {len(a_span)} chars, under the "
                                    f"{ag.MIN_SPAN_CHARS} minimum, so it is a heading "
                                    f"rather than the provision"))
            continue

        if as_anchors and not ag._anchor_present(a_span, as_anchors):
            as_bad.append((r["id"], f"span lacks every anchor {as_anchors}. "
                                    f"Span begins: {a_span[:70]}"))
            continue

        as_ok += 1
        a_note = f", anchors {as_anchors} found" if as_anchors else ""
        print(f"  ok    {r['id']:<12} {a_cid} s.{a_sec}  {len(a_span)} chars{a_note}")

    for rid, why in as_bad:
        print(f"  FAIL  {rid:<12} {why}")

    print(f"\n  {as_ok}/{len(as_rules)} would verify")
    return as_ok, len(as_rules)


def main():
    as_types = sys.argv[1:] or None
    engine = RuleEngine()
    ix = StatuteIndex()

    try:
        n = ix.build()
        print(f"statute index: {n} chunks")
    except Exception as e:
        print(f"statute index failed to build: {e}")
        print("Run: python tools/fetch_corpus.py")
        return 1

    as_types = as_types or list(engine.doc_types())
    a_ok = a_tot = 0
    for t in as_types:
        o, n = check(t, ix, engine)
        a_ok += o
        a_tot += n

    print(f"\n{'=' * 72}\n  overall {a_ok}/{a_tot} grounded rules would verify")
    if a_ok < a_tot:
        print("  Each FAIL above names its own cause. A missing statute file is a\n"
              "  fetch problem; a heading-only or anchor-less span is a retrieval\n"
              "  problem. They are not the same fix.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
