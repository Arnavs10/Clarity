# Labeling Guide

Read this before every session. The single biggest threat to this project is
labeling day 1 differently from day 6, because then the eval numbers measure your
mood rather than the system.

## The question you are answering

For each clause: **would a careful person want this flagged before they signed?**

Not "is this unusual". Not "do I dislike it". Would flagging it change what the
reader does next. If the answer is no, it is not risky, however dense the language.

## Decision rules

Apply these in order. The first one that fires wins.

1. If the clause only recites facts, defines terms, or sets out addresses and
   signature blocks, it is `boilerplate`, `risky: false`, `severity: none`.
2. If the clause is standard for its category and the terms sit inside normal
   ranges, pick the real category but keep `risky: false`.
3. If the clause is one-sided, unusually harsh, or contradicts a statutory
   position, mark `risky: true` and set severity.
4. If you cannot decide in about 30 seconds, hit Skip. Come back at the end of the
   session with fresh eyes. Do not guess, a guessed label is a permanent error in
   your answer key.

## Severity

| Severity | Meaning |
|---|---|
| `high` | Real money or real freedom at stake. Do not sign without changing this. |
| `medium` | Worth pushing back on. Signing anyway is survivable. |
| `low` | Slightly off-market. Mention it, do not fight over it. |
| `none` | Only for `risky: false`. |

Calibration check: if more than about a third of your risky clauses are `high`,
you are inflating. Reset by re-reading five clauses you already marked `high` and
asking whether you would genuinely walk away over each.

## Rationale

Required on every risky clause. One line, plain language, written for the person
signing rather than for a lawyer.

Good: `Deposit is 6 months rent, MTA suggests a 2 month ceiling`
Good: `Notice is 90 days for the employee and 30 for the employer`
Bad: `unfair`
Bad: `this clause is problematic and should be reviewed carefully`

The rationale is not decoration. In Phase 2 these become your few-shot examples,
so vague rationales produce a vague model.

## Grounding hint

Optional, but fill it in whenever a statute is genuinely the reason. Short form is
fine, `s.27 Contract Act` or `MTA deposit cap`. Phase 2 uses these to check whether
the retriever is finding the passage you had in mind.

## Consistency habits

- Label in blocks of 40 to 50, then stop. Accuracy drops hard after that.
- Start each session by re-reading the last 5 labels you wrote.
- Run `python tools/validate_labels.py` at the end of every session, without exception.
- If you change your mind about a rule, write it into this file and re-label the
  affected clauses. Do not carry two rules in your head.

## Common traps

**Flagging complexity instead of risk.** Long clauses are not automatically risky.
A three line clause that forfeits your deposit is worse than a page of definitions.

**Missing asymmetry.** The most common real problem in Indian rental and offer
documents is not a single harsh clause, it is the same right granted unequally to
the two sides. Always read notice, termination, and cure periods from both sides.

**Over-labeling non-competes.** In an Indian offer letter almost every non-compete
is unenforceable post-termination under s.27 Contract Act. That makes it `medium`
in most cases, not automatically `high`. It becomes `high` when it is stacked with
a bond or a long garden leave.

**Under-labeling IP assignment.** Assignment of everything you create, including
outside work hours with no prior-work carve-out, is a genuine `high` for a student
who has side projects and a public GitHub.

## Before you commit anything to a public repo

Redact every real name, address, phone number, salary figure, PAN, and employer
name in `data/raw/`. Replace with placeholders that keep the clause structure
intact. The clause text is what matters, the identities are not.
