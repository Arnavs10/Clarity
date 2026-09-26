# Clarity, explained

Two passes over the same project. The first is for anyone. The second is for someone
who wants to know how it is built.

---

# Part 1: the plain version

## The problem

You get a rent agreement, an offer letter, or a loan agreement. It runs to several
pages of dense legal language. You skim it, you do not really understand it, you sign
it. Months later you find out the deposit is gone, or you cannot leave without paying
a bond, or the side project on your GitHub belongs to your employer.

The information was never hidden. It was just written so that nobody reads it.

## What the tool does

You upload the document. A few minutes later you get a list like this:

> **Intellectual Property Rights.** This assigns work you create outside working
> hours and off the premises. Anything you build on your own time may vest with the
> employer.
> *Section 19, Copyright Act 1957*, and the passage of the Act is shown underneath.

> **Non-Compete.** A restriction on where you may work after you leave. Section 27 of
> the Contract Act voids agreements in restraint of trade.

Then a button that drafts a message asking for those clauses to change. You read it,
edit it, and send it yourself. The tool never sends anything.

## The part that makes it different

Most tools of this kind ask a language model "does this clause look risky?" and print
whatever comes back. That answer cannot be checked. When it is wrong you cannot see
why, and when it is right you cannot prove it.

This one splits the job in two.

A model **reads** the clause and pulls out plain facts: the deposit is six months of
rent, the notice period is ninety days, the non-compete runs for two years. It is
never asked for an opinion.

A separate **rule layer** applies the threshold: six months is above the two-month
benchmark, so flag it.

Then every flag is matched to the passage of law behind it, retrieved from the actual
bare Act. If that passage cannot be found and confirmed, **the flag is withheld
rather than shown**. There is no path in the code that displays a flag without its
source. A flag you cannot check is a flag you cannot trust.

## Where the rules come from

Each rule says where its authority comes from, and the interface says each in a
different voice rather than flattening all four into "risky".

- **Statutory.** The Act says it. Section 27 of the Contract Act voids agreements in
  restraint of trade, so any post-termination non-compete gets flagged, and the
  section text is shown.
- **Regulatory.** A regulator directs it. The RBI's 2023 circular requires a late
  payment penalty to be a separate charge, not interest added to the rate, and bars
  compounding it. That binds banks and NBFCs.
- **Policy.** Official but not binding. The Model Tenancy Act caps a residential
  deposit at two months, but it only applies in states that adopted it, so these read
  as "below the benchmark" and never as "illegal".
- **Norm.** No law on point. Notice periods, escalation rates. These are stated as
  drafting judgements: the term is one-sided or leaves something important undefined,
  and reasonable people can disagree. They are never dressed up as law.

## Checks before and around the model

Two checks run before any clause is read. The document is compared against the type
you chose, so an offer letter submitted as a loan agreement is refused rather than
audited as one. And facts that belong to the whole document are read once: whether a
late charge is stated anywhere, whether registration is mentioned, how long the lease
runs. A rule about the document answers to those, not to whichever clause it happened
to be reading.

The most important facts are read by fixed patterns rather than by the model, so the
findings that matter most come out identical on every run. And a fact the model
supplies is thrown away if the clause is not about that subject at all.

When the same problem appears in four clauses, you see one finding that says where it
was found, not four cards.

## What it does not do

It is not legal advice and does not pretend to be. It flags clauses for review.

It cannot catch a risky clause that no rule covers. Nothing flagged means nothing
matched, not that a document is safe.

It reads three document types. It has no OCR, so a scanned image of a contract
produces nothing.

The rules are for Indian law. The contract corpus is drawn from US filings, which is
closer to the real problem than it sounds: Indian offer letters are routinely built
from US templates, which is exactly why they contain non-competes that section 27
voids.

---

# Part 2: the technical version

## Architecture

```
document
   |
guard             checked against the chosen type. A clear mismatch withholds
   |              every finding and the draft
docscan           document-wide facts read once by pattern
   |
segment           heading patterns plus paragraphs, scored on content coverage
   |
triage            TF-IDF + logistic regression, recall-tuned
   |              off entirely below 25 clauses
plan  <-------+   the agent picks which tools this clause needs
   |          |
act           |   pattern reader first, then the model. Neither decides risk
   |          |
assess        |   the rule engine fires. Citations retrieved by section number
   |          |
verify -------+   rejects findings whose span does not match. Bounded retry
   |
aggregate         risks separated from reassurances
   |
negotiate         drafted, never sent
```

LangGraph holds the loop. The conditional edge out of `verify` is what makes it an
agent rather than a pipeline: a finding whose citation does not open at the section
its rule named is rejected, and the graph routes back to `plan` with a note, up to a
bounded number of retries. If the retries are exhausted the finding is dropped.

## The pattern reader and topic gates

`src/deterministic.py` owns five fields outright (`weekly_hours`,
`unfilled_placeholder`, `settlement_conditional`, `unpaid_work_period`,
`material_terms_elsewhere`). The model is never asked for them and anything it
volunteers is discarded. Bond, recovery and clawback facts from the same reader
override the model when their pattern matches. A determinism self-test runs twelve
passes per clause against a model answering at random and asserts the grounded
findings never change.

Everything else the model returns passes a **topic gate**: 67 fields each carry the
vocabulary a clause must contain before that fact is believed. Some gates are two-part,
so rent escalation needs both "rent" and a change verb, and loan assignment needs both a
transfer verb and a loan or rights noun. The gates apply to the reader and the model
alike.

Termination for sexual harassment is protected misconduct. A clause whose subject is
harassment, with no vague ground beside it, cannot produce a termination-ground fact,
so a zero-tolerance POSH policy is never presented as something to negotiate away.

`src/docscan.py` reads seven document-scope facts, prefixed `doc__`, before any clause:
whether a penal charge or prepayment charge is quantified, whether the quantum is
withheld, whether a Key Facts Statement is present, whether registration is mentioned,
the lease term in months, whether a notice period is mentioned. A rule that reads only
`doc__` facts is refused at load time unless it declares `scope: document`, and then it
reports once rather than once per clause.

Severity on the norm tier is capped at medium. A norm rule reaches high only through an
`escalate_to_high_when` condition it declares itself, so a capped six-month lock-in is
medium and a lock-in that forfeits the whole deposit is high.

## Segmentation

Real contracts number themselves half a dozen ways, sometimes two ways in the same
document. Several heading patterns are tried, each run through the full split-and-tidy
pipeline, and scored on **content coverage** rather than how tidy the output looks.
Paragraph splitting competes on the same terms, because for a letter written in prose
it is genuinely the right answer.

That check caught a real bug. One pattern produced beautifully sized clauses while
silently discarding 70 percent of the document, because everything before its first
match was dropped. Scoring on tidiness alone would have preferred it.

Segmentation has been the largest single source of missed findings in this project.
Ten separate failures are listed in the README, and every one was found by running a
real document rather than by the test suite.

## Deduplication and sampling

EDGAR dedupes by URL, but the same lease is filed by several companies at different
URLs. Documents are compared on **clause-digest overlap**, not on a hash of their
opening text: contracts of the same kind open with near-identical recitals, so a
head-comparison flagged genuinely different leases as copies of each other.

Clauses are then sampled round-robin across documents, so a sample of 150 comes from
20 documents rather than the first four.

## The triage router

TF-IDF plus logistic regression, trained on labels the rule engine generates rather
than labels anyone wrote by hand.

A phrase safety net sits in front of it: a clause containing unmistakable language
such as "minimum period", "liquidated damages" or "irrevocably assign" reaches
extraction whatever the classifier scores it. That exists because the router quietly
dropped a service bond clause phrased unlike anything in its training corpus, and the
missing finding then looked like a rule gap. A cost optimisation should not be able to
veto an obvious case.

Tuned for recall, not accuracy. A false positive costs a fraction of a rupee; a false
negative means a bond clause is never read. Training prints recall at six thresholds
and picks the highest one that still holds recall at 1.00. When the corpus grew from
552 to 612 clauses the chosen threshold stepped down from 0.25 to 0.2, giving up seven
points of cost saving because 0.25 had fallen to 0.96 recall.

The first version used one flat keyword list and labelled 60 percent of a real SEC
corpus positive, because "terminate", "breach" and "covenant" appear in nearly every
clause of a commercial agreement. Strong and weak signal tiers fixed it.

Triage is disabled entirely below 25 clauses. On a nine-clause offer letter the saving
is a few pennies and four of nine clauses were being dropped before anything read
them.

**This recall figure is in-distribution.** Both the training labels and the test labels
come from the rule engine, so 1.00 means the classifier agrees with the rule engine on
text that resembles its corpus. It is not a measurement of field recall on documents
the classifier has never seen a relative of.

## Extraction and the rule engine

The model returns `{"deposit_months": 6, "forfeiture_at_sole_discretion": true}`. The
rule engine decides `6 > 2`.

The field list is generated from `RuleEngine.required_params`, so adding a rule
automatically extends what gets extracted, and a field no rule reads is never
requested. Anything outside that set is dropped from the model's response before it
reaches the engine, so a hallucinated field cannot influence a decision.

Conditions support `gt`, `gte`, `lt`, `lte`, `gt_percentile`, and `absent`. That last
one exists because silence and denial are different facts: an IP assignment that never
mentions prior work sweeps it in, so a rule needs to fire on the absence of a carve-out
rather than only on an explicit refusal of one. The percentile form reads its threshold
from `data/norms.json`. If that file is absent the rule stays **silent** rather than
falling back to a guessed number.

The extraction prompt carries explicit negation instruction, because a clause that
mentions something in order to exclude it is not the same as a clause that claims it.
"Work created outside working hours remains yours" is a carve-out, not an assignment,
and reading it as the latter produced the only false positive the fair fixtures have
ever generated.

## Retrieval and verification

Two lookups. `by_section` goes straight to a numbered section, finds the marker, and
extracts to the next boundary. `by_query` is BM25 over sentence windows, for when no
rule named a section.

The verifier requires that the retrieved span **opens at the section the rule named**,
not merely that something came back from the right Act. There is a fault-injection
self-test: feed a deliberately wrong citation and assert the finding is rejected. It
cannot pass by finding nothing, which is how the previous version of that check passed
while measuring nothing at all.

## MCP server

Five tools on the official SDK: `lookup_statute`, `search_statutes`, `list_rules`,
`required_parameters`, `evaluate_clause`. They are the same callables the agent uses
internally, so there is no second code path to drift.

## Rate limiting and throughput

Free tiers are capped per minute, and the binding limit is not always the obvious one.
Groq caps **tokens** per minute; an extraction prompt runs to a couple of thousand, so
a request-based limiter looked correct and still produced 429s that read as "no risky
clauses found". Both limits are tracked, whichever binds first wins, and there is a
rolling token window with exponential backoff.

At 7000 tokens a minute this works out to roughly **three clauses a minute**. A twenty
clause offer letter takes several minutes for one user and a second user queues behind
the first. That is a free-tier constraint rather than a code path: on a paid tier the
same document finishes in seconds.

## Failure modes made visible

The recurring lesson of this build: **silence and safety must never look the same**.

- Zero findings could mean a clean contract, failed extraction, or withheld citations.
  `tools/diagnose.py` names which.
- A corpus scan that is entirely rate-limited aborts after two documents and prints the
  actual error rather than reporting a clean corpus.
- A truncated model response is repaired rather than discarded, keeping the complete
  keys. A greedy regex from the first brace to the last used to swallow a reasoning
  model's prose and JSON together, losing half of every document silently.
- A missing PDF library returns a readable 400 naming the cause, not a 500 traceback.
- Without an extraction backend the API returns `extraction_available: false` and a
  warning, so an empty result never renders as an all-clear.

## Metrics

| Metric | What it measures | Labels needed |
|---|---|---|
| Groundedness | Nineteen of nineteen grounded rules whose citation resolved and opened at the named section | No |
| Verification self-test | Injects a wrong citation, asserts rejection | No |
| Router recall | Share of risk-bearing clauses kept, in-distribution | No |
| LLM calls avoided | Share routed past the model | No |
| Behavioural test | False positives on fair fixtures, misses on harsh ones, per type | No |
| Regression suite | 137 checks, each pinning a defect that was found and fixed | No |
| Category accuracy | Against hand-validated labels | Yes, about 40 |

**Six of the seven need no labels at all.** That is a direct consequence of the
rule-based design: risk is decided deductively from statute, so there is no labelled
training set on the critical path. The seventh, category accuracy, has not been measured.

## Proving it works

`tools/selftest.py` runs matched pairs for all three document types, six fixtures in
total: the same contract written fairly and written harshly. It reports false positives on the fair one and
missed findings on the harsh one separately, and fails on either. Flagging everything
and flagging nothing are both failures, and a single accuracy number hides which one
you have.

Each type has a small required set and a larger optional set. A required finding is one
a rule must catch whenever its parameter is present, so a miss is a defect and fails the
run. An optional finding depends on the model noticing a particular field on a
particular run, so a lower count there is variance. Keeping them apart means a real
regression cannot hide inside normal fluctuation.

## What the self-test caught

Building the paired fixtures paid for itself immediately. The first run flagged one
finding on the harsh letter instead of six, and the report said "4 extraction errors"
without printing one. The cause was that `gpt-oss-120b` is a reasoning model: it writes
analysis before the answer, that analysis contains braces, and a greedy regex from the
first brace to the last swallowed prose and JSON together. Half of every document was
silently never read.

The second run passed extraction cleanly and exposed two genuine rule gaps that had
been invisible while the parser was eating the input: a bond rule that fired only on a
named figure, and an IP rule that required an explicit refusal of a carve-out rather
than silence.

The third exposed the triage router dropping four of nine clauses on a short document.

## Honest limitations

- Rules cannot catch a novel risky clause that fits none of them.
- Three document types only.
- No OCR, so scanned PDFs produce nothing.
- Recall on unseen documents is unmeasured and is the weaker half. What it shows is
  backed; what it misses is the risk. The pattern reader covers employment only.
- Documents over 60 clauses are refused with an estimate rather than read partially.
- Category accuracy is unmeasured. The scope lock named the evaluation numbers as
  never-cut, so this is the one commitment that was not met rather than an optional
  extra. It needs about forty hand-validated labels.
- `compute_norms` returned zero metrics on this corpus, so the two percentile rules,
  R-EMP-09 and R-RENT-08, stay silent. Both are low severity, and the other twenty-seven
  norm-tier rules fire on boolean conditions regardless. That is the intended failure
  mode.
- Corpus is US-drafted; the grounding is Indian.
- Single annotator for any validation labels, so no inter-annotator agreement.
- Router recall is in-distribution, not field recall.
- Throughput on a free tier is roughly three clauses a minute.

## Questions worth expecting

**How do you know a clause is risky?** Four bases. Statutory rules cite the section and
show retrieved text. Regulatory rules cite a regulator's direction. Policy rules are
labelled non-binding. Norm rules are stated as drafting judgements, never as law.

**How do you stop hallucination?** The model never makes the risk judgement. Citations
are retrieved by section number, not generated. A finding whose span does not open at
its named section is rejected, the loop retries, and if it still fails the finding is
withheld rather than shown.

**What if the law changes?** Edit one line of YAML. Nothing is retrained.

**How do you add a document type?** A profile and a rule file. The pipeline, the agent,
and the MCP server do not change. Three types ship this way.

**Who labelled your data?** Nobody had to for six of the seven metrics. Risk is deduced
from statute rather than learned from labels, which is why groundedness rather than
precision is the headline number. Category accuracy would need labels and has not been
measured.

**What is your weakest point?** Recall. On documents with deliberately planted
problems, a good share went unflagged: loan acceleration and cross-default, a missing
Key Facts Statement, landlord entry without notice. What it shows can be checked; what
it misses is invisible, which is why the interface lists the areas it looked at and
says anything outside them was not examined. Segmentation is the other answer: most of
the bugs found so far came from a layout the segmenter had not seen, and each was found
by running a real contract rather than by the test suite.
