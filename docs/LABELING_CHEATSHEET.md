# Labeling Cheatsheet

Keep this open next to the labeler. You are not deciding what is legal. You are
deciding what a careful person would want flagged before signing. That is a
judgement any informed adult can make, and it is the right target anyway, because
the tool flags things for human review rather than giving legal opinions.

## The five questions, in order

Stop at the first one that fires.

**1. Is this just definitions, recitals, addresses, or signature blocks?**
Then `boilerplate`, `risky: false`, `severity: none`. Done.

**2. Does one side get a right the other side does not?**
Then `risky: true`. This is the single most reliable test in the whole guide.
Read notice, termination, cure periods, and penalties from both sides every time.
Most real unfairness in contracts is asymmetry, not any single harsh sentence.

**3. Is a number outside the thresholds in the table below?**
Then `risky: true`.

**4. Does it match a "watch" note for its category?**
The watch note appears in the labeler as soon as you pick the category. If the
clause does what the watch note describes, `risky: true`.

**5. None of the above?**
`risky: false`. Pick the real category, not boilerplate.

## Thresholds

Employment:

| Thing | Normal | Flag it when |
|---|---|---|
| Notice period | Same on both sides | One side is shorter than the other |
| Probation | 3 to 6 months | Extendable with no stated limit |
| Service bond | Rare, tied to real training cost | Any bond over 2 years, or a penalty not linked to a cost |
| Non-compete after leaving | Should not exist in India | Any post-termination restraint at all |
| Non-solicit | 6 to 12 months, defined client list | No time limit, or no defined list |
| Confidentiality | 2 to 5 years after exit | Perpetual, or covering already-public information |
| IP assignment | Work product, during work | Anything created outside work hours, no prior-work carve-out |
| Clawback | Tied to misconduct or restatement | Triggered by plain resignation |

Rental:

| Thing | Normal | Flag it when |
|---|---|---|
| Security deposit | 2 months rent | Above 2 months |
| Deposit refund | Within 1 month of vacating | Longer, or "at sole discretion" |
| Landlord entry | 24 hours written notice | No notice, or entry at will |
| Rent escalation | 5 to 10 percent a year | Above 10 percent, or left undefined |
| Lock-in | Binds both sides equally | Binds only the tenant |
| Early exit penalty | Proportionate | Entire deposit forfeited |
| Repairs | Structural on landlord | All repairs pushed to tenant |

These are working thresholds for labeling, not legal advice. When something sits
right on the line, mark the lower severity.

## Severity in one line each

| | |
|---|---|
| `high` | Real money or real freedom at stake. Do not sign without changing it |
| `medium` | Worth pushing back on. Signing anyway is survivable |
| `low` | Slightly off market. Mention it, do not fight over it |

Calibration check: if more than about a third of your risky clauses are `high`, you
are inflating. Re-read five and ask whether you would genuinely walk away over each.

## Two specific calls people get wrong

**Non-competes.** In an Indian offer letter almost every post-termination non-compete
is void under section 27 of the Contract Act. That makes it `medium` in most cases,
not automatically `high`. It becomes `high` when stacked with a bond or long garden
leave.

**IP assignment.** Assignment of everything you create including outside work hours,
with no carve-out for prior work, is a genuine `high` for anyone with side projects
and a public GitHub.

## When you cannot decide in 30 seconds

Hit Skip. Come back at the end of the session. A guessed label is a permanent error
in your answer key and it is worse than a missing one.
