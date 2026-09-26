"""
Audit the rule set for the failures that reached real documents.

    python tools/lint_rules.py
    python tools/lint_rules.py --type loan

This does not decide whether a rule is right. It cannot: that is a reading of the
law and of the contract. What it does is surface the shapes that went wrong on
17 September 2026, so the remaining rules can be checked deliberately rather than
one at a time when a document happens to expose them.

Five checks:

  severity     a norm-tier rule claiming high without naming why. High is the
               strongest thing this tool says and on the norm tier nothing backs
               it, so a rule that wants high has to name the severe condition.

  scope        a rule that makes a claim about the whole agreement while reading
               only clause-level fields. "never", "nowhere", "no clause" and
               "anywhere" are words about a document. R-LOAN-04 said "never
               quantified" from one clause and was wrong on a real agreement.

  subject      a rule whose message names something none of its fields mention.
               R-RENT-09 said "rent may be revised" and fired on a clause about
               administrative charges, because nothing tied the message to the
               field.

  gates        a model-read field with no topic gate. Without one the model may
               assert that fact about any clause at all, which is how an offer
               letter produced findings about calling in a loan balance.

  citations    a grounded rule with no anchor phrase. Verification can then only
               check where a passage opens, not what it says, and a heading passed
               as the law a finding rested on.

Everything it prints is a question to answer, not a defect. Read the rule, then
either fix it or leave it and know why.
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import deterministic                     # noqa: E402
from rules import RuleEngine, _walk_fields   # noqa: E402

# Words that make a claim about the agreement rather than about one clause.
AS_DOC_WORDS = re.compile(
    r"\b(never|nowhere|no clause|not anywhere|anywhere in|throughout|"
    r"at no point|nothing in this)\b", re.I)

# Subject words worth checking a message against. If a message uses one of these
# and no field the rule reads mentions it, the message may be describing something
# the rule does not actually test for.
AS_SUBJECTS = {
    "rent": ["rent", "escalation", "lease_amount"],
    "deposit": ["deposit", "forfeit"],
    "notice": ["notice"],
    "interest": ["interest", "rate", "penal"],
    "penal": ["penal", "penalty", "late"],
    "balance": ["acceleration", "outstanding", "default"],
    "guarantee": ["guarantee", "surety"],
    "insurance": ["insurance"],
    "repossess": ["repossession", "seizure"],
    "jurisdiction": ["jurisdiction", "forum", "arbitr"],
    "lock-in": ["lock_in", "early_exit", "minimum"],
    "registration": ["registered", "registration", "term_months"],
    "assign": ["assignment", "transfer", "securitis"],
    "set off": ["set_off", "lien"],
}

AS_ISSUES = []


def flag(a_kind, a_id, a_note):
    AS_ISSUES.append((a_kind, a_id, a_note))


def lint(a_engine, a_type):
    for r in a_engine.rules_for(a_type):
        a_id = r["id"]
        a_msg = " ".join((r.get("message") or "").split())

        as_fields = set()
        _walk_fields(r["when"], as_fields)

        # 1. severity
        if (r["basis"] == "norm" and r.get("severity") == "high"
                and not r.get("escalate_to_high_when")):
            flag("severity", a_id,
                 "claims high on the norm tier without an escalate_to_high_when")

        # 2. scope
        if AS_DOC_WORDS.search(a_msg):
            if not any(f.startswith("doc__") for f in as_fields):
                a_word = AS_DOC_WORDS.search(a_msg).group(0)
                flag("scope", a_id,
                     f'message says "{a_word}", which is a claim about the whole '
                     f"agreement, but the rule reads only clause-level fields")

        # 3. subject
        a_joined = " ".join(as_fields).lower()
        for a_word, as_hints in AS_SUBJECTS.items():
            if re.search(rf"\b{re.escape(a_word)}", a_msg, re.I):
                if not any(h in a_joined for h in as_hints):
                    flag("subject", a_id,
                         f'message mentions "{a_word}" but no field it reads does '
                         f"({sorted(as_fields)})")
                break

        # 4. gates
        for f in sorted(as_fields):
            if f.startswith("doc__"):
                continue
            if f in deterministic.DETERMINISTIC_ONLY:
                continue
            if f not in deterministic.AS_GATES:
                flag("gates", a_id,
                     f'"{f}" is read from the model with no topic gate, so it can '
                     f"be asserted about any clause")

        # 5. citations
        if r["basis"] in ("statutory", "regulatory", "policy") and not r.get("anchors"):
            flag("citations", a_id,
                 "grounded rule with no anchor phrase: verification can check where "
                 "the passage opens but not what it says")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--type", help="lint one document type only")
    a_args = ap.parse_args()

    a_engine = RuleEngine()
    as_types = [a_args.type] if a_args.type else a_engine.doc_types()

    a_total = 0
    for t in as_types:
        a_total += len(a_engine.rules_for(t))
        lint(a_engine, t)

    print(f"Linting {a_total} rules across {len(as_types)} type(s).\n")

    if not AS_ISSUES:
        print("Nothing to review.")
        return 0

    as_kinds = {}
    for a_kind, a_id, a_note in AS_ISSUES:
        as_kinds.setdefault(a_kind, []).append((a_id, a_note))

    for a_kind in ("severity", "scope", "subject", "gates", "citations"):
        as_rows = as_kinds.get(a_kind)
        if not as_rows:
            continue
        print(f"{'=' * 72}\n{a_kind.upper()}  ({len(as_rows)})\n{'=' * 72}")
        for a_id, a_note in as_rows:
            print(f"  {a_id:<12} {a_note}")
        print()

    print(f"{len(AS_ISSUES)} things to look at. None of them is automatically a bug.")
    print("Read the rule, decide, and if you leave it, know why.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
