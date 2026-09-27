# Clarity

Grounded clause-risk review for Indian contracts.
**Live demo:** https://clarity-qn80.onrender.com

Upload a rent agreement, an offer letter or a loan agreement. An agent reads it
clause by clause, applies rules grounded in Indian statute, and returns each finding
with the exact statutory passage it relied on. It then drafts a negotiation message
you approve and send yourself.

Flags for review. Not legal advice.

A full walkthrough, in plain language and then technically, is in
[EXPLAINER.md](EXPLAINER.md).

## Why it is built this way

Most contract-risk tools ask a language model whether a clause looks risky. That is
undebuggable: when it is wrong you cannot see why, and when it is right you cannot
prove it. It also cannot be evaluated honestly, because the answer key ends up being
written by the same kind of model doing the work.

Here the two jobs are separated:

- a model **reads** each clause and extracts structured facts (`deposit_months: 6`)
- a **rule layer** applies the threshold (`6 > 2`)
- every rule names the source of its authority, and the citation is retrieved from
  the bare Act rather than generated
- a **verification node** rejects any finding whose citation does not open at the
  section the rule named, is too short to be the provision rather than its heading,
  or does not contain the rule's anchor phrase

A finding whose citation cannot be resolved and matched is **withheld, not shown**.
There is no fallback path that displays an uncited flag. A wrong answer is therefore
traceable to a specific extracted number or a specific rule, and thresholds change by
editing one line of YAML with nothing retrained.

## Basis tiers

Every rule declares where its authority comes from, and the interface says each in a
different voice rather than flattening all four into "risky".

| Tier | Meaning | How it reads | Rules |
|---|---|---|---|
| `statutory` | The Act says it | "Section 27 voids agreements in restraint of trade" | 11 |
| `regulatory` | A regulator directs it, binding on the entities it regulates | "The RBI requires this to be a separate penal charge" | 4 |
| `policy` | Official but non-binding | "Below the national policy benchmark" | 4 |
| `norm` | No law on point | "This is a drafting judgement, not a point of law" | 29 |

**48 rules across three document types**, 19 employment, 15 rental, 14 loan.
Eleven are statutory, four regulatory, four policy, twenty-nine norm-tier.

The nineteen grounded rules cite a written source and are therefore verifiable. The
other twenty-nine are norm-tier and carry no citation by design.

The norm tier used to be described as "unusual compared with similar contracts". That
wording asserted a dataset that does not exist: `tools/compute_norms.py` returned zero
metrics, so twenty-seven of the twenty-nine are hand-written drafting judgements and
the remaining two stay switched off. The tier is now described as what it is.

A norm rule also cannot claim high severity without naming, in its own YAML, the
condition that makes the case severe. High is the strongest thing this tool says, and
on this tier there is no statute, no regulator and no measured corpus behind it, so a
rule that wants high has to show its working or it is held at medium.

Statutory sources are official bare Acts from India Code. Regulatory rules cite the
RBI circular on penal charges dated 18 August 2023, verified against the text on
rbi.org.in. Policy rules cite the Model Tenancy Act 2021, which binds only in states
that adopted it, and say so.

Three rules were deleted during verification rather than shipped on recall: two Model
Tenancy Act claims the Act does not actually make, and one service-bond precedent that
could not be sourced.

## Results

| Metric | Value | What it measures |
|---|---|---|
| Groundedness | **19/19** | Every statutory, regulatory and policy rule whose citation resolved and opened at the section it named. Employment 7/7, rental 7/7, loan 5/5 |
| Verification self-test | **PASS** | A deliberately wrong citation is injected and the finding is rejected |
| Router recall | **1.00** | Risk-bearing clauses the triage classifier keeps, held-out split of 612 clauses |
| LLM calls avoided | **49%** | $2.45 to $1.26 per 612 clauses at $0.004 a call |
| Router latency | **0.34 ms** | Per clause |
| Behavioural test | **3 of 3 types PASS** | Flags the harsh fixtures, stays silent on the fair ones |
| Regression suite | **137 checks PASS** | Pins every defect found, each check traceable to one failure |
| Unseen documents | **3 audited** | One real offer letter and two generated agreements on the live model. No false positive in the findings reviewed after v37.3. Recall not measured |

Reproduce them:

```bash
python src/router.py --train employment
python src/router.py --benchmark employment
python tools/evaluate.py --all employment
python tools/selftest.py
python tools/selftest_v37.py
```

### What these numbers do not say

**Groundedness is 19 of 19, not a percentage over a large sample.** It checks that
every rule claiming a source can actually produce that source, and that the retrieved
span opens where the rule said it would. It says nothing about whether the right rule
fired on the right clause.

**Router recall is in-distribution.** The labels it trains and tests against are
generated by the rule engine, not written by hand, so the number measures agreement
with the rule engine on text resembling the training corpus. It is not field recall.
The router did once drop a real service bond clause phrased unlike anything in that
corpus, which is why a phrase-based safety net now overrides it.

**Category accuracy is not measured.** It needs about 40 hand-validated labels that
have not been done. Groundedness, verification and the router numbers need no labels
at all, which is why they are the headline figures.

## Does it actually work

```bash
python tools/selftest.py
```

Six fixtures in `tests/fixtures/`, a matched pair per document type. Each pair is the
same contract written fairly and written harshly: a fair offer letter and the same
letter with a service bond, an asymmetric notice period and IP reaching outside work
hours; a fair rent agreement and one with a six month deposit and sole-discretion
forfeiture; a fair loan agreement with a separate uncompounded penal charge and one
where the penalty is added to the rate and compounded.

A tool that flags the harsh one and stays quiet on the fair one is working. One that
flags both is crying wolf; one that flags neither is decoration. The test reports
false positives on the fair fixture and missed findings on the harsh one separately,
per document type, and fails on either.

### Required and optional findings

Each type has a small required set and a larger optional set.

| Type | Required, a miss fails the run | Optional |
|---|---|---|
| employment | 3: service bond, IP reaching outside working hours, post-termination non-compete | 6 |
| rental | 3: deposit above the benchmark, forfeiture at sole discretion, whole-deposit forfeiture on any breach | 5 |
| loan | 2: penal interest added to the rate, penal charges compounded | 6 |

Required findings are the ones a rule must catch every time. Optional findings depend
on the model noticing a particular field in a particular clause and vary between runs
on the same fixture; a typical run catches 5 of 6 optional on employment and 5 of 5 on
rental. The distinction is deliberate rather than a way of lowering the bar: a rule
that fails to fire when its parameter is present is a defect, while a model that
misses a parameter on one run and catches it on the next is variance, and conflating
the two would hide the first.

Two rules in the whole set are blocked without computed norms, because they compare a
number against a percentile of the corpus: R-EMP-09 (garden leave length, low) and
R-RENT-08 (escalation rate, low). Both stay silent rather than fire on a guessed
threshold. The other twenty-seven norm-tier rules fire on boolean conditions and work
today, which is why norm-tier findings still appear in every run.

Rental and loan fixtures exist because rules for those types had never touched a
document. A rule file that has only ever been read, never run, is a guess. Both have
since run on generated agreements neither the author nor the reviewer wrote. What
broke, and what was done about it, is recorded in [docs/SCOPE.md](docs/SCOPE.md).

## Which document to demo

```bash
python tools/scan.py employment --report
```

Runs the pipeline over every document of a type, ranks them by what came back, and
names the one worth recording a demo on. It also lists which rules never fired on this
corpus, which is information rather than a fault: a corpus can simply lack a clause
type.

## When a document comes back with nothing

Zero findings is ambiguous. The contract may be clean, extraction may be returning
nothing, or citations may be unresolvable and every finding withheld. Those look
identical in the report, which is the worst property a safety tool can have.

```bash
python tools/diagnose.py data/raw/employment_07.txt employment
```

It walks one document and prints what happened at each stage: corpus and citation
health, provider status, clause count, what the model returned per clause, which
parameters survived, which rules fired, and which findings were withheld. It ends by
naming which situation you are in.

## If uploads fail while pasted text works

The server is running under an interpreter without `pdfplumber`. Activate the project
virtualenv and restart. The API says so in the response rather than returning a 500,
because a traceback in the browser reads as "this application is broken" when the
actual fault is which Python started it.

## Architecture

```
document
   |
guard              is this the type the user chose? A clear mismatch withholds
   |               every finding and the draft
docscan            document-wide facts read once by pattern: is a penal charge
   |               quantified anywhere, is registration mentioned, the lease term
segment            heading patterns plus paragraphs, scored on content coverage
   |
triage             TF-IDF + logistic regression, recall-tuned. 49% skip the LLM
   |               (off entirely below 25 clauses)
plan  <-------+    agent picks which tools this clause needs
   |          |
act           |    pattern reader first, model for the rest. It never decides risk
   |          |
assess        |    rule engine fires. Citations retrieved by section number
   |          |
verify -------+    rejects findings whose span does not match. Bounded retry
   |
aggregate          risks separated from reassurances
   |
negotiate          drafted, never sent
```

Triage is switched off below 25 clauses. It exists to avoid paying for a model call on
a clause that cannot matter, which is most of the cost on a 500-clause commercial
agreement and a few pennies on a nine-clause offer letter. On the short document the
saving is not worth the recall.

## Two readers

The model is not the only thing that reads a clause.

`src/deterministic.py` reads five fields by pattern and owns them outright:
`weekly_hours`, `unfilled_placeholder`, `settlement_conditional`,
`unpaid_work_period` and `material_terms_elsewhere`. The model is never asked for
them, and any value it volunteers is discarded. It also reads bond, recovery and
clawback facts, which override the model whenever the pattern matches. The findings
that matter most are therefore identical on every run: twelve runs per clause against
a model answering at random produced the same grounded findings each time.

For everything the model does read, 67 fields carry a **topic gate**. A fact is
discarded when the clause contains none of the vocabulary that fact is about, so a
clause confirming a degree cannot produce `acceleration_without_notice`. Some gates
need two things at once: rent escalation needs both "rent" and a change verb.

Termination for sexual harassment is recognised as protected misconduct. A
zero-tolerance POSH clause is not a vague termination ground, and flagging it would
put a request to weaken a harassment policy into the reader's own negotiation message.
If the same clause also adds a vague ground ("harassment or poor performance"), it is
flagged as before.

## Stack

FastAPI backend, LangGraph agent loop, an MCP server exposing the tool layer,
scikit-learn for the triage router, BM25 retrieval over the statute corpus, a static
web interface, and a Manifest V3 browser extension.

## Run it

```bash
pip install -r requirements.txt

python src/audit.py --demo employment          # no API key needed
python src/agent.py --demo loan --trace        # see the graph path

python tools/fetch_corpus.py                   # statutes from India Code, RBI, PRS
python tools/fetch_documents.py --type employment --n 20
python src/segment.py --all employment

cp .env.example .env                           # paste a key into .env, not the shell
python src/llm.py                              # confirms it is reachable
python src/llm.py --list                       # what this key can actually reach

python tools/selftest.py
python tools/evaluate.py --all employment

uvicorn src.api:app --reload --port 8000       # backend
python -m http.server 5500 --directory web     # then open localhost:5500
                                               # (or localhost:8000, which serves the page too)

python mcp_server/server.py                    # MCP tool layer
streamlit run app.py                           # alternative interface
```

## Model providers

The extractor and the drafter call `src/llm.py`, which speaks to four backends behind
one interface. Switching is an environment variable, not a rewrite.

| Provider | Key | Cost |
|---|---|---|
| `groq` | `GROQ_API_KEY` from console.groq.com | Free tier, no card, highest rate |
| `gemini` | `GEMINI_API_KEY` from aistudio.google.com | Free tier, no card, lower rate |
| `anthropic` | `ANTHROPIC_API_KEY` | Prepaid credits |
| `ollama` | none, runs locally | Free, no network |

Put the key in `.env` rather than a shell export. `.env` is gitignored, and an exported
key ends up in shell history and in any screenshot of that terminal. A stale export
also silently shadows the file, so `python src/llm.py` reports which source a key came
from and warns when the two differ.

### Throughput, and why it matters for a public demo

Free tiers are rate limited per minute, so calls are paced and retried with backoff.
Two limits are tracked, because they are not the same limit: requests per minute and
tokens per minute. Groq's free tier caps tokens, and an extraction prompt runs to
roughly two thousand, so a request-only pace sails past the token ceiling and returns
429s that read as a quiet corpus.

At 7000 tokens a minute that works out to **about three clauses a minute**. A twenty
clause offer letter takes several minutes for a single user, and a second user queues
behind the first. Override with `LLM_RPM` and `LLM_TPM`, or move to a paid tier where
the same document finishes in seconds. Any public deployment should either sit on a
paid tier or make the wait explicit in the interface.

### Providers

Four providers are supported and the choice is one line in `.env`. What matters for
this workload is not raw speed but how many clauses you can audit in a day, because
one clause is one call.

| | Free without a card | Binding limit |
|---|---|---|
| `groq` | yes | 200,000 tokens/day, roughly two self-test runs |
| `gemini` | yes | counted in requests per day, which suits per-clause work |
| `cerebras` | **no longer** | 5 dollars of credit, expires in 30 days |
| `ollama` | yes, runs locally | your own hardware, nothing leaves the machine |

Groq is the default. Gemini is the better choice once Groq's daily ceiling starts
biting, since a token ceiling is the wrong shape for work that makes many small
calls.

Cerebras ended its no-card free tier on 17 August 2026. Comparison articles written
before that date still describe a 1,000,000 token daily allowance; it is gone, and a
key created now returns HTTP 402 until a payment method is added.

An exhausted quota is not a neutral event. It is the condition under which every
clause goes unread, so check `tools/evaluate.py --verification` behaviour and use
`--offline` when testing rather than burning the day's tokens on a rehearsal.

### Privacy

Clarity stores nothing. A document is read in memory, segmented, audited and
discarded when the response is returned. It is not written to disk, not persisted to
a database, and there is no account or history. The interface says so at the point of
upload, because an offer letter carries a name and a salary and a lease carries an
address, and a tool that keeps those has taken more than it needed to answer the
question.

That is true of this codebase, and until v37.5 it was not: every audit left a
clause-by-clause copy of the document in `data/clauses/`. See `docs/SCOPE.md` Part 5.
One endpoint stores by design: `/versions/save` keeps the drafts it compares under
`data/versions/`, which is gitignored. The page never calls it.

None of that is automatically true of the model provider, so read its terms rather
than assume. Groq, the default, states that it does not train on API data and does not
retain inference requests by default (console.groq.com/docs/your-data). Gemini's free
tier is different: Google may use what is sent to improve its products. That is fine
for public filings and the fixtures in this repo, and **not** fine for someone's real
offer letter. The deployed version runs on Groq.

## Public demo

The deployed page works exactly like a local run: upload an offer letter, a rent
agreement or a loan agreement and it is audited live. It runs on Groq's free tier, so
the allowance is about 200,000 tokens a day, which is a few full documents, each taking
several minutes. When the day's allowance runs out, clauses come back marked unread
rather than clean, and the page says so.

The page also offers recorded audits of fictional documents, made from live runs, for
anyone who wants to see the output without waiting. To record one, start the backend
and run the recorder on a **fictional** document:

```bash
python tools/record_samples.py rental ~/Downloads/rent_agreement.pdf
```

It refuses to save when a clause went unread, when the document did not read as its
type, when the draft failed, or when a word listed in `.prepush-deny` appears in the
result.

To publish:

```bash
python tools/prepush_check.py                  # fails on anything private
git push                                       # Render redeploys the latest commit automatically
```

GROQ_API_KEY is set as an environment variable in Render's dashboard, not in the repo. `.prepush-deny` is a
gitignored file with one term per line: the names that must never be published.
`push_space.sh` gives the Space its own README header, pins packages to the versions
installed locally, and leaves out the statute index, which is rebuilt on first start.

The Dockerfile sets `CLARITY_PUBLIC=1` and `CLARITY_PUBLIC_LIVE=1`: live audits stay
on, one at a time, and version comparison is off, because it stores the drafts it
compares. Remove `CLARITY_PUBLIC_LIVE` for a samples-only showcase.

## Comparing drafts

The landlord sends v2. Did they fix it, or reword it so it looks fixed?

```bash
python src/versions.py save my_lease v1 draft1.pdf rental
python src/versions.py save my_lease v2 draft2.pdf rental
python src/versions.py diff my_lease v1 v2
```

Clauses are matched across drafts in three passes: identical text, then heading, then
token overlap. Each finding comes back as `RESOLVED`, `PERSISTED`, `NEW` or
`REWORDED`.

`REWORDED` is the one this exists for. The clause changed and the finding survived,
which is the clearest signal an edit was cosmetic.

## Browser extension

`extension/` is a Manifest V3 extension that flags clauses on pages you are about to
accept. Load it unpacked from `chrome://extensions` with developer mode on, and run
the API on port 8000.

Three gates decide whether the user is interrupted, and all three must pass: the
finding must be `statutory`, `regulatory` or `policy` basis, it must be `verified`,
and it must be `high` severity. Norm-tier findings are market conventions rather than
law, so they appear in the popup but never raise an alert. There is a per-domain mute
and a 30 day cache.

## Interface

The web page states a verdict in a sentence before it lists anything, because a count
of findings is not an answer to "should I sign this". Clauses and findings are linked
both ways: clicking a clause highlights its findings, clicking a finding scrolls to the
clause. The summary tiles filter the view.

The explanatory page describes what a flag means and where its reasoning comes from,
without describing how the system is built. That belongs here and in EXPLAINER.md, for
a reader who wants it, rather than in front of someone who just wants to know whether
to sign.

Failures are written as sentences with a next step rather than shown as raw exceptions,
and every render path is exercised in Node before shipping. A crash in the interface
previously blanked the findings list while the backend had worked correctly, which is a
worse failure than showing nothing at all.

## Document types are configuration

A type is one YAML profile in `config/types/` plus one rule file in `config/rules/`.
Adding a fourth means writing those two files. The pipeline, the agent, and the MCP
server do not change.

## Layout

```
config/types/     taxonomy, corpus, legal anchors per document type
config/rules/     the rules, each with a basis tier and a source
src/doctype_guard.py  checks the document against the chosen type
src/docscan.py    document-level facts, read once by pattern
src/segment.py    clause segmentation
src/deterministic.py  pattern reader and topic gates
src/rules.py      rule engine
src/retrieve.py   statute retrieval, exact section lookup plus BM25
src/extract.py    model-side parameter extraction
src/agent.py      LangGraph loop with the verification node
src/router.py     triage classifier
src/versions.py   cross-draft comparison
src/negotiate.py  negotiation drafting
src/api.py        FastAPI backend
mcp_server/       MCP server, five tools
tools/            corpus and document fetchers, norms, evaluation, diagnostics,
                  prepush_check.py, record_samples.py
tests/fixtures/   matched fair and harsh pairs per document type
web/              the interface
web/samples/      recorded audits of fictional documents, for the public demo
extension/        Manifest V3 browser extension
app.py            Streamlit interface
Dockerfile        the public demo's container
deploy/           legacy Hugging Face push script, unused since the move to Render
```

## Limits

- Rules cannot catch a novel risky clause that fits no rule. Nothing flagged means
  nothing matched, not that a document is safe.
- Only three document types. An NDA, a partnership deed or an insurance policy will be
  read but the rules will not fit.
- No OCR. A scanned PDF produces nothing.
- **Recall on unseen documents is unmeasured, and it is the weaker half.** Every
  finding shown is backed, but on documents with deliberately planted problems a
  good share went unflagged: loan acceleration and cross-default, a missing Key
  Facts Statement, landlord entry without notice, a perpetual social-media gag in an
  offer letter. The pattern reader covers employment only; rental and loan depend on
  the model reading each clause.
- Documents over 60 clauses are refused with a time estimate rather than read
  partially. Set `CLARITY_MAX_CLAUSES` to change it.
- Category accuracy is unmeasured. The scope lock named the evaluation numbers as
  never-cut, so this is the one commitment that was not met rather than an optional
  extra. It needs about forty hand-validated labels.
- `tools/compute_norms.py` returned zero metrics on this corpus: 40 clauses yielded one
  or two numeric samples per field against a floor of eight. Two rules depend on it,
  R-EMP-09 and R-RENT-08, both low severity, and they stay silent rather than fire on a
  guessed threshold. That is the intended failure mode and a smaller gap than it
  sounds: the other twenty-seven norm-tier rules fire on boolean conditions and work
  today.
- The corpus is drawn from SEC EDGAR filings, so clause language is US-drafted. That is
  closer to the real problem than it sounds: Indian offer letters are routinely built
  from US templates, which is why they contain non-competes that section 27 voids.
- Single annotator for any validation labels, so no inter-annotator agreement.
- **Segmentation has been the largest single source of missed findings**, not the rules
  and not the model. Each of these was found by testing on a real document rather than
  by the test suite:
  - A per-page letterhead matched as an all-caps heading, so a three page offer letter
    split into three page-sized blocks and produced zero findings.
  - Headings formatted `05 CONFIDENTIALITY`, with no punctuation after the number,
    matched nothing.
  - Repeating-line removal needed three occurrences, so a two page contract kept its
    letterhead on every clause. Two pages is the common case, not the edge case.
  - Segmentation scoring rewarded blocks between 200 and 3000 characters, so a correct
    three-way split of a short offer letter scored zero while leaving the page in one
    piece scored one.
  - Prose headings such as "In plain language" matched nothing, so a letter written
    without numbering came back as two clauses.
  - Headings ending in a colon, which is how most Indian offer letters are written,
    matched nothing. A four page letter came back as four clauses with its notice
    period and service commitment never read.
  - Footers numbered per page ("1 Highly Confidential", "4 Highly Confidential") never
    registered as repeats, so they sat inside clauses.
  - Section banners that changed on every page ("Clauses 4-10 | ...") stayed inside the
    clauses below them.
  - A plain-numbered section after dotted subsections ("12.4" then "13. Notices") was
    read as a continuation. Six sections of a real offer letter vanished into the clause
    above, including its governing law.
  - The fix for numbered footers compared every line with its digits masked, which
    made "Late fee Rs. 500" and "Late fee Rs. 750" look like one repeating line and
    deleted both. An EMI schedule would have gone the same way. Digits are now masked
    only on lines that read like furniture.

  All ten are fixed. It is worth assuming other layouts will break it in ways not yet
  seen.
- Two rule gaps were found by the self-test rather than by reading the rules. A bond
  fired only on a named figure, so one phrased as "serve three years and pay liquidated
  damages" was missed. And an IP assignment rule required an explicit "no carve-out"
  when the commonest and riskiest form says nothing about prior work at all. Both now
  fire on the wider condition, and the engine gained an `absent` operator so a rule can
  distinguish "the clause says no" from "the clause is silent".
- The triage router silently dropped a service bond clause before extraction because it
  was trained on a corpus that phrased bonds differently. A phrase-based safety net now
  forces any clause containing language like "minimum period", "liquidated damages" or
  "irrevocably assign" through to extraction regardless of the classifier score, and
  the self-test reports skipped clauses so this cannot hide again.
- Not legal advice, and the interface never claims a norm-tier finding is unlawful.
