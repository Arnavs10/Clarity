# Scope

Locked 13 August 2026. Shipped 10 September 2026.

This file records what was committed to at the start and what actually happened,
including the two commitments that were not met. It is kept in the repo deliberately:
a scope document that quietly matches the outcome is not evidence of anything.

---

# Part 1: the lock, as written on 13 August

## What this is

A grounded clause-risk auditor. You upload a contract, an agent reads it clause by
clause, decides which clauses carry risk, retrieves the statute or market-norm text
that supports that judgement, and returns a finding with a span-level citation
pointing at the exact line it relied on. It then drafts a negotiation message you
approve before sending.

It is not legal advice. Every output is framed as a flag for human review.

## In scope

- Clause segmentation from PDF and DOCX
- Per-type clause taxonomy and severity model
- Hybrid retrieval over a curated statute corpus
- Agent loop with tool selection, bounded retry, and a verification node
- MCP server exposing the tool layer
- Span-level citation on every finding
- ML triage router that filters boilerplate before the expensive path
- Version comparison across two drafts of the same document
- Negotiation message drafting behind a human approval gate
- Evaluation harness
- Web UI, public deployment, README, demo video

## Out of scope

- Jurisdictions outside India
- Clause rewriting or redlining inside the document itself
- Signature workflows, e-stamping, registration
- Multi-user accounts, billing, teams
- Mobile app
- Fine-tuning any model

## Never cut

These four are the project. Everything else is decoration.

1. The MCP server
2. The agent loop with the verification node
3. Span-level citations on every finding
4. The evaluation numbers

## Cut order under time pressure

1. Version comparison across drafts
2. One document type
3. The React frontend, dropping to a static page

---

# Part 2: outcome, 10 September 2026

## Document types

Two were locked. **Three shipped.**

| Type | Slug | Grounded rules | Norm rules | Total |
|---|---|---|---|---|
| Employment offer letter | `employment` | 7 statutory | 12 | 19 |
| Residential rental agreement | `rental` | 3 statutory, 4 policy | 8 | 15 |
| Loan agreement | `loan` | 1 statutory, 4 regulatory | 9 | 14 |

**48 rules in total**: 11 statutory, 4 regulatory, 4 policy, 29 norm. Nineteen are
grounded and carry a citation the reader can open.

Loan agreements were added on 31 August when the timeline extended, grounded in the
RBI penal charges circular of 18 August 2023 and verified against the text on
rbi.org.in. Adding the type also introduced a fourth basis tier, `regulatory`, because
a regulator's direction is neither an Act nor optional guidance and flattening it into
either would have misstated what it is.

**This is scope growth, not scope drift.** Nothing in the locked list was dropped to
make room for it, and it cost one YAML profile plus one rule file, which is what the
type registry was designed to prove.

## The four "never cut" items

| Commitment | Status |
|---|---|
| MCP server | Shipped. Five tools, same callables the agent uses internally |
| Agent loop with verification node | Shipped. Bounded retry, findings withheld when citations fail |
| Span-level citations on every finding | Shipped for all 19 grounded rules. Norm rules carry no citation by design and are stated as drafting judgements |
| The evaluation numbers | **Partially met. See below** |

## The metric commitment, and how it changed

The lock named **risky vs not-risky precision and recall per type** as the headline
metric, with n=100 hand-labelled clauses per type supporting it.

**That metric was never produced, and the reason is a design change rather than a
missed deadline.**

On 22 August the project moved from a model-judges-risk design to a rules-decide-risk
design. Under the original design, precision and recall against human labels was the
only way to know whether the tool worked. Under the rules design, risk is deduced from
statute, so the question changed from "does the model agree with a human" to "does
every rule produce the source it claims, and does the tool flag a harsh contract while
staying silent on a fair one".

What shipped instead:

| Metric | Value | Labels needed |
|---|---|---|
| Groundedness | 19/19 grounded rules resolved and matched their named section | No |
| Verification self-test | PASS, injected wrong citation rejected | No |
| Router recall | 1.00, in-distribution, 612 clauses | No |
| LLM calls avoided | 49% | No |
| Behavioural test | 3 of 3 types pass, 0 false positives on fair fixtures | No |
| Category accuracy | **Not measured** | Yes, about 40 |

Five of six need no labels. The sixth was not done.

**This is the honest position:** the numbers that shipped are stronger evidence for a
rules-based tool than per-type precision would have been, but the specific commitment
in the lock was not met, and category accuracy remains unmeasured. A reader is entitled
to hold that against the project.

## The other unmet commitment

`tools/compute_norms.py` was written to compute percentile thresholds from the corpus
so that no norm-tier number was chosen by hand. It **returned zero metrics**: scanning
40 clauses per type produced one or two numeric samples per field against a floor of
eight, because the corpus is US SEC filings that rarely state deposits in months or
notice in the Indian format.

Two rules depend on it, R-EMP-09 and R-RENT-08. Both stay silent rather than firing on
a guessed threshold. The other 22 norm-tier rules fire on boolean conditions and work
today.

## Cut order: nothing was cut

Version comparison shipped. All three document types shipped. The frontend is a static
page rather than React, which was the third cut and was taken deliberately for time
rather than under pressure.

## Added beyond the lock

- A fourth basis tier, `regulatory`
- Matched fair and harsh fixtures for every document type, six in total, and a
  behavioural self-test that fails on either a false positive or a missed required
  finding
- `tools/diagnose.py`, which names which of three situations a zero-finding result is
- `tools/scan.py`, which ranks a corpus by what came back
- A Manifest V3 browser extension
- An `absent` operator in the rule engine, so a rule can distinguish "the clause says
  no" from "the clause is silent"

## Parking lot, still parked

- NDA as a fourth type
- Clause rewriting suggestions
- Comparison against a corpus of previously audited contracts
- Regional language support
- OCR for scanned documents

---

# Part 3: 17 September 2026, the first unseen documents

Rental and loan had never run on an agreement neither the author nor the reviewer
wrote. Three documents were audited: a generated rent agreement, a generated loan
agreement, and, by mistake, a real internship offer letter submitted as a loan
agreement. Nine findings came back. None was clean.

This section records what broke, because a scope document that stops at the last
thing that went well is not evidence of anything.

## What the three runs showed

| Run | Findings | Verdict |
|---|---|---|
| Rent agreement, 22 clauses | 2 | one mislabelled, one over-severe |
| Loan agreement, 27 clauses | 4 | one factually wrong, two over-stated, one correct |
| Offer letter read as a loan agreement, 40 clauses | 3 | all three meaningless |

The single correct finding was R-LOAN-04's retrieval: the RBI passage it produced
genuinely says what the finding claimed. The retrieval and verification half of the
thesis held. The judgement half did not.

## The six defects, and what was done

**1. Nothing checked the document against the type.** The dropdown was trusted
absolutely. `src/doctype_guard.py` now scores the text against a vocabulary for each
type before any clause is read. On a clear mismatch the findings are withheld
entirely, not merely labelled, and the negotiation draft is withheld with them.

**2. A fact was trusted about a clause that was not on that subject.** The model
returned `acceleration_without_notice` for a clause in which a candidate confirms his
degree. Topic gates in `src/deterministic.py` now discard a model-supplied fact when
the clause contains none of the vocabulary that fact is about.

**3. A rule claimed something about the document from one clause.** R-LOAN-04 said
penal charges were never quantified, reading the auto-debit clause, while clause 7 of
the same agreement set the late charge at 2% per month. `src/docscan.py` reads
document-scoped facts once, by pattern, and the rule now answers to them.

**4. Silence was invisible.** R-RENT-10 asked for `registered: false`, which a lease
with no registration clause never produces, so the rule could not fire on exactly the
documents it exists for. It now reads a document-scope fact instead.

**5. Severity did not track harm.** A capped six month lock-in and a set-off clause
requiring notice were both high, while a 5% prepayment fee was medium. R-RENT-04 and
R-LOAN-08 were graded down on that evidence, and the norm tier now has to justify
high in the rule itself.

**6. Page furniture was inside the clauses.** Footers numbered per page never
registered as repeats, and page banners carrying the page number never registered at
all. One clause of the loan agreement was swallowed whole by the clause above it and
was never examined. `src/segment.py` now masks digits before comparing lines and
strips the top and bottom band of each page.

`tools/selftest_v37.py` pins all six. Sixty-seven checks, each traceable to one of
the failures above.

## What is still not done

Category accuracy remains unmeasured. It was the one unmet never-cut commitment in
August and it is still unmet. Nothing in this round addressed it.

Recall on unseen documents is still unmeasured, and the three runs above suggest it
is the weaker half: on documents that deliberately planted flaggable clauses, roughly
a quarter were found. The misses are listed in the working notes and several sit in
categories the tool claims to check.

---

# Part 4: 20 to 22 September 2026, the live model on real documents

The three documents from Part 3 were run again on the live model after the v37 fixes,
along with a real internship offer letter submitted correctly as an offer letter. Five
new defects came out of those runs, and two more came out of reading the screenshots
closely. All seven are fixed.

## What broke

**1. A sexual harassment policy was flagged as a vague termination ground.** The POSH
clause of a real offer letter ended in termination with immediate effect, and the
negotiation draft asked the employer to narrow its harassment policy and add a cure
period for harassment. The trigger matched how the termination was worded without
asking what it was for. Termination for harassment is now protected misconduct unless
the same clause adds a vague ground, and the word "perform" no longer counts as a
performance ground on its own ("activities performed at any other site" matched it).

**2. Six sections of the same letter were never read.** A plain-numbered section after
dotted subsections ("12.4" then "13. Notices") was taken as a continuation, so sections
13 to 17, and section 4 earlier, vanished into the clause above. The letter came back as
40 clauses instead of 47, and its governing-law clause was never examined. A plain
section now splits when it is the next number, has a short title and does not end in a
comma or semicolon, which keeps a numbered list inside a clause intact. The clause
ceiling rose from 40 to 60.

**3. Section banners stayed inside clauses.** Banners such as "PRICING AND SERVICING /
Clauses 4-10 | ..." change on every page, so repeat detection never caught them. They
are now recognised by the navigation line and removed with the capitals title above it.

**4. The registration rule still did not fire on a 12-month lease.** The term was stated
in a key-terms table ("Term 01 October 2026 to 30 September 2027 (12 months)") with no
"of" anywhere, and the pattern expected "a term of". A second pattern reads a term
stated beside the word Term; a lock-in ("minimum commitment of six months") does not
match it.

**5. One problem was shown as four.** A termination finding that fired on four clauses
produced four identical cards and was counted as eight findings. Findings are now
grouped by rule, with the worst severity kept, the matched text merged, and a link to
each clause. The verdict counts problems, and the draft argues each one once.

**6. Fee tables were being deleted, a regression introduced in v37.** The Part 3 fix for
numbered footers compared every line with its digits masked, so "Late fee Rs. 500" and
"Late fee Rs. 750" looked like one repeating line and both were deleted. An EMI schedule
would have gone the same way. It did not affect the documents tested, but it would have
affected the first public loan agreement with a fee table. Digits are now masked only on
lines that read like furniture.

**7. The negotiation draft could fail without anyone seeing it.** It had no loading state
and swallowed errors, so a draft still coming and a draft that had failed both looked
like empty space. It now says it is being written, and says plainly when it could not be.

## What the live runs showed after the fixes

| Document | Clauses | Findings | Verdict |
|---|---|---|---|
| Real internship offer letter | 47 | 3 issues across 7 clauses, all medium | Correct. Harassment clause not flagged. Non-compete and moral rights grounded in sections 27 and 57 |
| Generated rent agreement | 22 | 3 issues: 1 high, 2 medium, 2 grounded | Deposit correctly high, grounded in the Model Tenancy Act |
| Generated loan agreement | 27 | 3 issues, all medium | Correct. The false "penal charges never quantified" finding from Part 3 is gone |

No false positive was found among the findings reviewed on any of the three. `tools/selftest_v37.py` now holds 108
checks, each traceable to a failure in Part 3 or Part 4.

## What is still not done

**Recall is the weaker half and remains unmeasured.** On the documents above, several
problems in categories the tool claims to check went unflagged: acceleration and
cross-default in the loan, a missing Key Facts Statement, landlord entry without notice
in the rent agreement, and a perpetual ban on naming the employer on social media in the
offer letter. The pattern reader covers employment only, so rental and loan depend on
the model reading each clause. That is the largest lever left.

**Category accuracy remains unmeasured.** The harness is ready and needs about forty
hand-validated labels. It is still the one unmet never-cut commitment from August.

---

# Part 5: 22 September 2026, before publishing

Reading the code for what a public push would publish turned up a defect worse than
any finding error.

**The privacy note was false.** The page and the README said a contract was never
written to disk. Every audit wrote a complete clause-by-clause copy of it into
`data/clauses/`, a folder that was not gitignored and that the router and the
labelling queue read from. Any document audited through the page left a copy there,
including the real offer letters used in Part 4. The code now reads uploads from
memory and saves nothing, a regression test proves it through the real endpoints, and
`tools/prepush_check.py` fails any push that would publish private text.

**A test named the employer of a real letter.** Fixed, along with three smaller
references to the same letter.

**Deployment.** The public version runs live audits on Groq's free tier, the same as a
local run, with recorded audits of fictional documents offered beside the upload box
for anyone who does not want to wait. `tools/selftest_v37.py` now holds 137 checks.

Recall and category accuracy are untouched by this part. Both remain unmeasured.

**The live runs of 23 September.** The rent and loan agreements and three real offer
letters were run through the page on the live model. Every citation shown was real
and verified, and the type guard caught a letter submitted as a rent agreement. Six
things were wrong, and all six are fixed:

1. The negotiation draft for the rent agreement said the lease "states that" a
   twelve-month lease requires registration, and put Clarity's own finding in
   quotation marks. The check that exists to catch invented quotes split on straight
   quotation marks only; the model writes curly ones.
2. R-RENT-10 fired on a twelve-month lease while quoting section 17, which says "for
   any term exceeding one year". Part 4 made it fire at twelve months on purpose. The
   passage it cites says otherwise, so it now fires above twelve.
3. A confidentiality clause limited to "non-public information" was reported as
   having no carve-out for public information.
4. A background-check clause was reported as a broad termination ground, while the
   same letter's real one, termination "for policy violations or unsatisfactory
   performance", went unflagged. Both are corrected.
5. Eleven to eight across six days was reported as more than 48 working hours. With
   the rest hour the Acts require, it is 48. It is still raised, at medium; high now
   needs the week to exceed 48 after that hour.
6. The verdict counted clauses in its headline, occurrences in its tile and issues in
   its summary, left low findings out of the sum, and told the reader a blank in a
   template "would cost you real money or real freedom".

Still arguable and left as they are: the set-off finding on a clause that already
requires notice, and the lock-in rule, whose message now claims only what the clause
it read can show.

**The live runs of 24 September.** The same five documents were run again on v37.6.
Every fix held: no invented quotes, the registration finding gone from the twelve-month
lease, the background-check clause silent and the real termination ground caught,
hours at medium, and a verdict whose numbers add up. Fifteen issues were shown and
graded against the source documents: fourteen correct, one arguable, none wrong.

One miss remained, and it mattered. The fictional lease states "Security Deposit INR
94,500 (equivalent to three months' rent)" in its key-terms table, and the model, which
had caught it on an earlier run, missed it on this one. v37.7 reads the deposit from
the page, in months where the lease says months and by dividing by the stated monthly
rent where it only gives money, so the rule most leases in India break no longer rests
on the model reading one long opening block.
