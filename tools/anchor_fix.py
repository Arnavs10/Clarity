"""
Give every grounded rule an anchor phrase, verified against your own corpus.

    python tools/anchor_fix.py              # report only, changes nothing
    python tools/anchor_fix.py --write      # apply the anchors that verified

Why this matters
----------------
Verification has three gates: the span opens at the named section, it is long
enough to be a provision rather than a heading, and it contains the rule's anchor
phrase. Eleven of the nineteen grounded rules carry no anchor, so only the first
two gates run on them.

agent.py already records what the first gate alone permits. The one-line heading
"74. Compensation for breach of contract where penalty stipulated for" opened in
the right place and passed as the law a finding rested on, as did a page of a
research group's navigation text. The anchor is the gate that asks what the
passage says rather than where it starts.

Why this is a tool and not a patch
----------------------------------
An anchor has to be a phrase that appears in the corpus **you** actually have.
India Code prints amendment markers, soft hyphens where a word broke across a
line, and whatever the OCR made of the page. An anchor written from memory of the
Act, or from a different edition, fails the gate, and a failed gate withholds the
finding silently. That trades a loud error for a quiet one, which is worse.

So nothing here is written on faith. For each rule this retrieves the span the
rule's own source resolves to, tests every candidate against it using the same
_anchor_present the verifier uses, and writes only what passed. Anything that did
not pass is printed with the span, so you can read the text and choose a phrase
from it yourself.

Run tools/check_corpus.py afterwards. It must still be 19/19.
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agent import _anchor_present          # noqa: E402  the real gate, not a copy
from retrieve import StatuteIndex          # noqa: E402
from rules import RuleEngine               # noqa: E402

# Candidates per rule, most specific first.
#
# These are phrases from the provisions themselves, not paraphrases of them. The
# order matters: a longer phrase is a stronger gate, so the first one that
# verifies against your corpus is the one written. If none verify, nothing is
# written for that rule and the span is printed instead.
#
# _anchor_present requires every word longer than two characters to be present in
# the normalised span, not the exact string, so punctuation and amendment markers
# between the words do not defeat a candidate. Word order is not required either,
# which is why a candidate has to be specific enough that the words appearing
# together somewhere in the provision actually means something.
AS_CANDIDATES = {
    # Indian Contract Act 1872, section 27
    "R-EMP-01": ["restrained from exercising a lawful profession",
                 "restrained from exercising",
                 "to that extent void"],
    "R-EMP-02": ["restrained from exercising a lawful profession",
                 "restrained from exercising",
                 "to that extent void"],

    # Indian Contract Act 1872, section 74
    "R-EMP-11": ["reasonable compensation not exceeding",
                 "whether or not actual damage or loss is proved",
                 "reasonable compensation"],
    "R-RENT-05": ["reasonable compensation not exceeding",
                  "whether or not actual damage or loss is proved",
                  "reasonable compensation"],

    # Copyright Act 1957, section 19, mode of assignment
    "R-EMP-05": ["assignment of the copyright shall be valid unless it is in writing",
                 "in writing signed by the assignor",
                 "assignment of the copyright"],

    # Copyright Act 1957, section 57, author's special rights
    "R-EMP-07": ["right to claim authorship of the work",
                 "distortion mutilation or other modification",
                 "independently of the author's copyright",
                 "claim authorship"],

    # Registration Act 1908, section 17
    "R-RENT-10": ["leases of immovable property from year to year",
                  "for any term exceeding one year",
                  "reserving a yearly rent",
                  "term exceeding one year"],

    # RBI penal charges circular, 18 August 2023
    "R-LOAN-01": ["shall not be levied in the form of penal interest",
                  "treated as penal charges and shall not be levied",
                  "penal interest that is added to the rate of interest",
                  "shall be treated as penal charges"],
    "R-LOAN-02": ["no capitalisation of penal charges",
                  "no further interest computed on such charges",
                  "capitalisation of penal charges"],
    "R-LOAN-03": ["shall not be higher than the penal charges applicable to non individual borrowers",
                  "penal charges applicable to non individual borrowers",
                  "individual borrowers for purposes other than business"],
    "R-LOAN-04": ["quantum and reason for penal charges shall be clearly disclosed",
                  "disclosed by REs to the customers in the loan agreement",
                  "most important terms and conditions Key Fact Statement",
                  "quantum and reason for penal charges"],
}

GROUNDED = ("statutory", "regulatory", "policy")


def rule_yaml_path(a_type):
    return ROOT / "config" / "rules" / f"{a_type}.yaml"


def insert_anchor(a_path, a_id, as_anchors):
    """
    Add an anchors line to one rule, leaving every comment in the file alone.

    Round-tripping through yaml.dump would reformat the file and delete the
    commentary, which is most of what these files are worth. So this finds the
    rule block and inserts one line into it.
    """
    s = a_path.read_text()
    a_start = s.index(f"- id: {a_id}")
    m = re.search(r"\n  - id: ", s[a_start:])
    a_end = a_start + m.start() + 1 if m else len(s)
    a_block = s[a_start:a_end]

    if "anchors:" in a_block:
        return False, "already has anchors"

    # After the source block, so the anchor sits with the citation it guards.
    m = re.search(r"\n(    (?:severity|message|note|escalate_to_high_when):)", a_block)
    if not m:
        return False, "could not find an insertion point"

    a_line = "    anchors: [" + ", ".join(f'"{x}"' for x in as_anchors) + "]\n"
    a_new = a_block[:m.start() + 1] + a_line + a_block[m.start() + 1:]
    a_path.write_text(s[:a_start] + a_new + s[a_end:])
    return True, "written"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true",
                    help="apply the anchors that verified against your corpus")
    a_args = ap.parse_args()

    a_engine = RuleEngine()
    a_index = StatuteIndex()
    if not a_index.chunks:
        print("Building the corpus index. This needs data/corpus to be populated;")
        print("run tools/fetch_corpus.py first if it is empty.")
        a_index.build()

    as_todo = []
    for a_type in a_engine.doc_types():
        for r in a_engine.rules_for(a_type):
            if r["basis"] in GROUNDED and not r.get("anchors"):
                as_todo.append((a_type, r))

    if not as_todo:
        print("Every grounded rule already carries an anchor. Nothing to do.")
        return 0

    print(f"{len(as_todo)} grounded rules have no anchor phrase.\n")
    print("For each one: retrieve the span its own source resolves to, then test")
    print("candidate phrases against that span with the verifier's own check.\n")

    n_ok = n_manual = 0
    as_writes = []

    for a_type, r in as_todo:
        a_id = r["id"]
        print("=" * 72)
        print(f"{a_id}   {r['basis']}   {a_type}")
        print(f"  source: {r['source']}")

        # Candidates are tested against the span the verifier would actually see,
        # which means widening the same way it does.
        a_hit = a_index.cite(r["source"], anchors=AS_CANDIDATES.get(a_id))
        if not a_hit or not a_hit.get("text"):
            print("  RETRIEVAL FAILED. This rule cannot resolve its own source, which")
            print("  is a bigger problem than the missing anchor. Check the corpus_id")
            print("  and the section number, then run tools/check_corpus.py.")
            n_manual += 1
            print()
            continue

        a_span = " ".join(a_hit["text"].split())
        print(f"  retrieved {len(a_span)} chars from {a_hit.get('corpus_id')}")

        as_cands = AS_CANDIDATES.get(a_id, [])
        if not as_cands:
            print("  no candidates listed for this rule, choose one from the span below")
            _show(a_span)
            n_manual += 1
            print()
            continue

        a_chosen = None
        for a_cand in as_cands:
            a_pass = _anchor_present(a_span, [a_cand])
            print(f"    {'PASS' if a_pass else 'fail'}  {a_cand!r}")
            if a_pass and a_chosen is None:
                a_chosen = a_cand

        if a_chosen:
            print(f"  -> would write: anchors: [{a_chosen!r}]")
            as_writes.append((a_type, a_id, [a_chosen]))
            n_ok += 1
        else:
            print("  NONE VERIFIED. Your corpus does not contain these words together.")
            print("  Read the span and pick a phrase out of it:")
            _show(a_span)
            n_manual += 1
        print()

    print("=" * 72)
    print(f"{n_ok} verified against your corpus, {n_manual} need a phrase chosen by hand.")

    if not a_args.write:
        print("\nReport only. Re-run with --write to apply the verified anchors.")
        return 0

    print()
    for a_type, a_id, as_anchors in as_writes:
        a_ok, a_why = insert_anchor(rule_yaml_path(a_type), a_id, as_anchors)
        print(f"  {a_id:<12} {a_why}")

    # A malformed insertion would only show up on the next audit, so it is caught
    # here instead: reloading proves the files still parse and the rules still load.
    try:
        RuleEngine()
        print("\nRules reload cleanly.")
    except Exception as e:                                   # noqa: BLE001
        print(f"\nRULES NO LONGER LOAD: {e}")
        print("Restore config/rules from git and re-run without --write.")
        return 1

    print("\nNow run:  python tools/check_corpus.py")
    print("It must still report 19/19. An anchor that verified here will verify")
    print("there, so a drop means something else moved.")
    return 0


def _show(a_span, a_width=76):
    print()
    for i in range(0, min(len(a_span), 900), a_width):
        print(f"    | {a_span[i:i + a_width]}")
    if len(a_span) > 900:
        print(f"    | ... ({len(a_span) - 900} more chars)")


if __name__ == "__main__":
    sys.exit(main())
