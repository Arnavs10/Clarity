# v37.8

Deployment settings and the sample banner, 26 September. No Python code changed.

## The Space would have run with public mode off

That left the version endpoints open, and they store the drafts they compare. It also
let two audits share one per-minute budget at the same time. The Dockerfile now sets
`CLARITY_PUBLIC=1` and `CLARITY_PUBLIC_LIVE=1`: live audits stay on, one at a time,
and the version endpoints return 403. Checked by running the server with those
settings: page 200, audit allowed, `/versions/save` and `/versions/{id}` refused.

## The sample banner

It read like a note to the developer, with a version tag and a line about the model.
It now says what a visitor needs: this is a sample, the document is fictional, and the
output is Clarity's own, from a live run. This entry was also first filed at the bottom
of the file, and the recorder reads the version from the first line, so new samples
were labelled v37.7. It sits on top now.

# v37.7

From the live runs of 24 September. Each change is pinned in `tools/selftest_v37.py`
section 11 (8 checks). The reader's output on every fixture clause is unchanged.

## The deposit is read from the page

A fictional lease stating "Security Deposit INR 94,500 (equivalent to three months'
rent)" in its key-terms table was flagged high on one run and missed on the next: the
model reads that long opening block least reliably. `docscan` now reads
`doc__deposit_months` itself, from months stated in words or, where only money is
given, by dividing by a monthly rent that appears as exactly one figure. A maintenance
charge or a late fee is not taken for the rent, and two different rents give no
answer. R-RENT-01 reads it and is now document-scope, so it reports once.

## Drafts ask on the merits

A draft point with no law behind it said "No statute settles this point", which is
true and which, sent to an employer or a landlord, concedes the point before asking.
It now cites nothing and asks for the change on its merits. The page still labels these
findings "No law on point".

## Evidence opens at its own sentence

Broad-cause evidence began with the previous sentence's last word ("certificate. The
Company..."). The sentence match now starts on a non-space.

# v37.6

Six defects from the live runs of 23 September, each pinned in `tools/selftest_v37.py`
section 10 (13 checks). The reader's output on every fixture clause is unchanged.

## The draft quoted the finding as if the lease said it

`validate_draft()` split quotations on `"` only, and the model writes curly quotes, so
an invented quotation passed. Quotes are normalised before the check. A finding that
is true of the whole agreement (`scope: document`) is no longer handed the opening
clause as "the clause", so the model is not invited to quote it.

## R-RENT-10 at twelve months

It fired at twelve months while citing section 17's "for any term exceeding one year".
It now fires above twelve, says "more than one year", and anchors on that phrase.

## Reader patterns

"Non-public information" counts as a carve-out for public information. A background
or document check clause is not a termination-for-cause clause unless it names a
conduct or performance ground. A right to terminate "for policy violations or
unsatisfactory performance" with no notice or cure in the sentence is now read as a
broad ground; the noun "termination" alone is not, so a non-compete running "following
the termination of your employment for any reason" stays silent.

## Working hours

The reader also computes `weekly_hours_net`, the week less an hour's rest on any day
longer than five hours. R-EMP-16 still fires on the span, at medium, and is high only
when the net week exceeds 48. The message says the span counts breaks.

## The verdict

The headline counts issues and no longer promises money or freedom over a drafting
defect. The summary includes low findings, so its numbers add up, and the high tile
counts problems, as the summary does.

## Wording

R-RENT-04 claims only what the clause it read shows. A docstring naming a real
employer is now generic.

# v37.5

## The page said nothing was stored, and every audit was saved

The privacy note on the page said a contract was not written to disk, and the README
said that was true of the codebase. It was not. `segment.run()` always wrote the
clauses it produced to `data/clauses/<name>.jsonl`, and the API called it on a
temporary file, so every audit left a complete clause-by-clause copy of the document
under a random name. The folder is not gitignored, and the router and the labelling
queue both read every file in it. The Streamlit app also left each upload in the temp
directory for good.

Saving is now something the corpus builder asks for (`--all`, or `--save` for one
file). The API and the Streamlit app read uploads from memory: `read_bytes()` parses
PDF, DOCX and text from a stream and `split_text()` segments without touching disk, so
the sentence on the page is now literally true. Segmentation is unchanged: 21 of 21
document and type combinations produce identical clauses, offsets and ids, and PDF and
DOCX read identically from memory and from disk.

## The employer of a real letter was named in a test

A regression test for mixed numbering quoted a real letter's clause with the
employer's name, and a docstring, a code comment and this changelog carried the city,
the recipient and the date. All now use neutral text. No test depended on the words
that changed.

## Nothing checked what a push would publish

`tools/prepush_check.py` checks exactly what `git add -A` would stage plus what is
already committed: paths that only ever hold private text, clause rows from audited
uploads in the queue or the training files, words listed in a gitignored
`.prepush-deny` searched as raw bytes (the router's pickled vocabulary is plain text),
git history, and file sizes. It caught all seven leaks planted to test it.

## Deployment

The API now serves the page at `/`, so one address is the whole app. `Dockerfile` and
`deploy/push_space.sh` publish it to a Hugging Face Space, where it runs live audits
exactly as it does locally, on Groq's free tier. `tools/record_samples.py` records
audits of fictional documents from live runs, and the page offers them beside the
upload box for anyone who does not want to wait. `CLARITY_PUBLIC=1` is available for a
samples-only deployment and is not set.

## Tests

`tools/selftest_v37.py` section 9, eight checks. The two no-disk checks were run
against the v37.4 behaviour and fail on it. The recorder's grouping of findings was
compared with the page's own JavaScript in node and matched exactly; a hash of the
page's grouping code catches any later drift.

# v37.4

## A regression introduced in v37: fee tables were being deleted

To recognise footers numbered per page ("1 Highly Confidential", "4 Highly
Confidential"), v37 compared every short line with its digits masked. That also made
"Late fee Rs. 500" and "Late fee Rs. 750" the same line, and deleted both as a
repeating footer. An EMI schedule, where rows differ only in dates and amounts, would
have been deleted entire. Clause text reaching the model would have lost the figures a
penal-charge or fee rule needs.

It did not affect the three documents tested on 20 and 22 September, whose clause
counts and text were checked and are unchanged. It would have affected the first public
loan agreement with a fee table.

Digits are now masked only on lines that read like furniture: a page marker, a
confidentiality stamp, a test banner, or a letterhead marker. Numbered footers are still
removed; fee tables and schedules are kept. Three regression tests pin it.

## The draft could fail without anyone seeing it

The negotiation draft is a second request after the audit. It had no loading state, and
on a server error or a dropped connection it returned silently, so a draft still coming
and a draft that had failed both looked like empty space. On the offer-letter run of 22
September it was impossible to tell which had happened. The draft now shows "Writing it
now" while it waits, and says plainly when it could not be written, with the reason.

Also: matched text that differs only in punctuation ("terminated forthwith." and
"terminated forthwith") is shown once on a grouped finding card.

tools/selftest_v37.py: 108 checks.

---

# v37.3

Found by running three real documents through the live Groq model on 20 September
2026, after v37.2. The six v37 fixes all held on real documents: the rent-revision
and penal-charges false positives were gone, lock-in and set-off came back at
medium, and a deposit of three months' rent was caught and grounded in MTA s.11,
which v36 had missed entirely. These are what that run exposed.

## A negotiation draft asked an employer to weaken its harassment policy

R-EMP-10 fired on an internship letter's sexual-harassment clause, because its
note ended "it would result in termination with immediate effect". The draft then
asked the employer to "limit the definition to conduct that is objectively unlawful
and provide a reasonable cure period". Sent under the candidate's name, that letter
would do them real harm.

The deterministic reader now asks what a clause is about before calling its
termination language unfair. A clause whose subject is sexual harassment is left
alone unless it also names a genuinely vague ground, such as performance, conduct
"prejudicial to the interest" or sole discretion, which is still a fair flag. The
same test closes the model's road, because reader facts are applied after the topic
gates and the gates alone never saw this. "Activities performed at any other site"
inside the harassment clause's own definition of the workplace had tripped the
vague-ground test, so it now looks for the noun "performance", not the verb.

## Six clauses of a real letter were never read

The segmenter picks one heading style per document. A letter numbered "12.4." for
most clauses and plain "13." for short ones was split on the dotted form alone, so
Performance, Notices, Successors and Assigns, Severability, Dispute Resolution and
Governing Law all vanished into the clause above. Governing Law carried an
exclusive-jurisdiction term that no rule could see.

A plain "N." line now opens a section when it is the next section number, reads as
a short title, and does not end in a comma. All three are needed: the same letter's
harassment clause lists its definitions "1. unwelcome sexual advances, ... 11.
humiliating treatment", and a looser test would have cut that one clause into
eleven. A bare heading such as "12. Termination" now sits over the clause it heads
instead of dangling off the end of the one before.

The letter went from 40 clauses to 47, over the old ceiling of 40, so a correctly
read ordinary document would have been refused. The ceiling is now 60, still
configurable with CLARITY_MAX_CLAUSES. Triage still skips boilerplate before any
model call.

No other document moved: all six fixtures and all three real offer letters segment
to exactly the same count as before.

## Section banners inside clauses

"PRICING AND SERVICING / INTEREST, FEES AND PAYMENT ADMINISTRATION / Clauses 4-10 |
..." still landed inside the preceding clause. The navigation line is now the
anchor: it is removed on sight, with the capital title lines directly above it. A
capital heading on its own is never removed, because removing a real heading merges
its clause into the one before.

## A 12-month unregistered lease was not flagged

The lease stated its term as a table row, "Term 01 October 2026 to 30 September
2027 (12 months)", and the reader wanted "term of 12 months". It now reads that
form, "Lease Period: 24 months", years, and "11 (eleven) months", which is the most
common way an Indian lease states its term and was also being missed. R-RENT-10 now
fires on the real lease, citing the lease clause of s.17(1)(d).

## The same finding repeated

One rule firing on four clauses showed four identical cards and was counted as four
findings. The page now shows one card per problem, lists every clause it appears in
as a jump link, keeps the worst severity and every matched phrase, and counts
problems rather than occurrences. The negotiation draft receives one entry per
problem.

## Anchors ship pre-written

All eleven were verified against the real statute corpus by tools/anchor_fix.py and
confirmed by tools/check_corpus.py at 19/19, so they are no longer something a fresh
unzip can silently erase.

## Tests

tools/selftest_v37.py now runs 105 checks, 23 of them new, each tied to one of the
defects above.

---

# v37

Everything here comes from one afternoon. On 17 September 2026 rental and loan ran
for the first time on agreements neither I nor the reviewer had written, and an
internship offer letter went through by mistake as a loan agreement. Nine findings
came back across three runs. None of them was clean.

The retrieval half of the thesis held: the one grounded finding produced an RBI
passage that genuinely says what the finding claimed. The judgement half did not.

## What was wrong, and what changed

### 1. Nothing checked the document against the type

An internship offer letter was audited as a loan agreement. `_run` checked that the
slug was one of three configured types and never checked the document. The loan rules
read it, reported that the entire balance could be called in with no notice, and
attached that to the clause where the candidate confirms his degree. The negotiation
draft then quoted that clause back under that heading.

**New:** `src/doctype_guard.py`. A vocabulary per type, matched whole-word over the
whole document, scored by distinct terms present rather than by how often they occur.
Deterministic, so it cannot vary between runs. It warns rather than refuses, because
a guard that blocks is a guard that is wrong about somebody's unusual but genuine
contract.

On a clear mismatch the findings are withheld entirely rather than labelled, and the
draft is withheld with them. Under a warning a reader reads the findings and skims the
warning.

Tested on every fixture against every type: 30 of 30 correct.

### 2. A fact was trusted about a clause that was not on that subject

The model returned `acceleration_without_notice` for a clause about educational
qualifications, `assignment_without_notice` and `jurisdiction_lender_city_only` for a
clause about absence without permission, and `escalation_undefined` for a rent
agreement's administrative-charges clause. Every rule was right. Every extraction was
wrong, and a rule cannot tell the difference.

**New:** topic gates in `src/deterministic.py`. A model-supplied fact is discarded
when the clause contains none of the vocabulary that fact is about. A gate can require
two things at once, because "escalation_undefined" needs both the rent and the change:
a deposit clause saying "equal to two months rent" mentions rent without being about
raising it.

Forty-eight fields gated. `tools/lint_rules.py` reports any that are not.

### 3. A rule made a claim about the document from a single clause

R-LOAN-04 said penal charges were never quantified. It fired on the auto-debit clause.
Clause 7 of the same agreement set the late charge at 2% per month. "Never" is a word
about a document.

**New:** `src/docscan.py` reads document-scoped facts once, by pattern, before any
clause is examined, under a `doc__` prefix so a rule opts into document scope visibly.
R-LOAN-04 now needs the document to agree with it, through either branch: the document
never quantifies, or it expressly withholds the quantum.

The window that looks for a figure stops at clause boundaries. Without that, the
heading "3. DEFAULT INTEREST" reached backwards into clause 2, found the principal of
the loan and reported the default interest as quantified when it was not. That error
suppresses true findings, which is the expensive direction.

### 4. Silence was invisible

R-RENT-10 asked for `registered: false`. A lease with no registration clause never
produces that field, the engine sees `None`, and the rule silently could not fire on
exactly the documents it exists to catch.

**Changed:** it reads `doc__registration_mentioned` and `doc__term_months` instead,
and carries `scope: document` so the finding is reported once rather than on all
twenty-two clauses. The engine refuses at load time any rule that reads only
document-scope fields without a clause-level anchor.

### 5. Severity did not track harm

A six month lock-in with a capped exit fee, and a set-off clause limited to matured
amounts and requiring notice, were both reported high. A five percent prepayment fee
that costs real money was medium.

**Changed:** a norm-tier rule reaches high only through an `escalate_to_high_when` it
declares itself. On that tier there is no statute, no regulator and, since
`compute_norms` returned nothing, no measured corpus, so a rule that wants the
strongest word this tool has must name what makes the case severe.

Ten rules already named the adverse condition in their trigger and now say so
explicitly. Two were graded down on the evidence of the real documents:

- **R-RENT-04** medium, high when leaving early forfeits the deposit. Its message no
  longer asserts an asymmetry inferred from silence.
- **R-LOAN-08** medium, high when set-off needs no notice. Its message no longer drops
  the three qualifiers the clause actually contained.

### 6. Page furniture was inside the clauses

Footers numbered per page hashed differently every time, so they never reached the
repeat threshold: "1 Highly Confidential", "4 Highly Confidential" and "6 Highly
Confidential" all survived into the middle of clauses. Page banners carrying the page
number never repeated at all. One clause of the loan agreement was swallowed whole by
the clause above it and was never examined by anything.

**Changed:** `strip_repeating_lines` masks digits before comparing, so numbered
footers are recognised as one footer. `strip_page_furniture` works on the top and
bottom band of each page and removes a line that either repeats once masked or carries
an explicit page marker. `read_text` keeps page boundaries so the positional check has
something to work with.

On a two page PDF carrying the exact furniture from the real documents: 7 clauses with
2 polluted, down to 6 clauses with 0 polluted, and the clause count is now right.

## Tests

`tools/selftest_v37.py`, 67 checks, every one traceable to a failure above.

| | |
|---|---|
| guard | 9 |
| gates | 16 |
| docscan | 13 |
| rules | 13 |
| segmentation | 6 |
| end to end, through the real HTTP handler | 10 |

The end-to-end section runs an offer letter through `api._run` as a loan agreement
with a model that returns loan facts for every clause it is shown. That is not a
caricature, it is the transcript from 17 September. Zero findings survive.

Also verified over real HTTP against a running uvicorn: the mismatch case, the correct
type, a PDF upload with page furniture, and twelve adversarial inputs including empty
text, 60k characters, Devanagari, null bytes, an unknown doc type, a junk `.pdf` and a
document over the clause ceiling. No 500s.

`tools/selftest.py --offline` produces output identical to v36 except for one line:
R-RENT-04 is now medium. Nothing else moved, so the gates suppressed nothing.

## New

- `src/doctype_guard.py`
- `src/docscan.py`
- `tools/selftest_v37.py`
- `tools/lint_rules.py`

## Two tools for the gaps that remain

### `tools/anchor_fix.py`

Eleven grounded rules carry no anchor, so only two of the three verification gates
run on them. This closes that, but it will not write an anchor it has not proved.

For each rule it retrieves the span that rule's own `source` resolves to **in your
corpus**, tests candidate phrases against it using `agent._anchor_present`, the same
function the verifier uses, and writes only what passed. Anything that fails is
printed alongside the span so you can pick a phrase out of the text you actually
have.

    python tools/anchor_fix.py            # report only
    python tools/anchor_fix.py --write    # apply what verified
    python tools/check_corpus.py          # must still be 19/19

Candidates are provided for all eleven, taken from the provisions themselves:
section 27 and section 74 of the Contract Act, sections 19 and 57 of the Copyright
Act, section 17 of the Registration Act, and the four claims in the RBI penal
charges circular.

**The anchors are not pre-written into this zip on purpose.** They were verified
here against a stub corpus written for the test, which proves the mechanism and
nothing about your text. An anchor that does not match your corpus fails the gate
and withholds the finding silently, which trades a loud error for a quiet one. Run
it against your own corpus and it takes thirty seconds.

### `tools/evaluate.py --classification-all`

The category accuracy metric had a bug that may be why it never produced a number.
`classification()` checked `ANTHROPIC_API_KEY` by name, while `llm.py` routes to
whichever provider `LLM_PROVIDER` names. On a Groq setup the check failed and the
metric reported "not measured" for a reason that had nothing to do with labels or
with the model. It now asks `llm.available()`.

Also added:

- a **Wilson confidence interval**. At n=40 an 80% result carries roughly twelve
  points either way, and the bare figure claims a precision the sample does not
  have. Wilson rather than the normal approximation because at 40/40 the textbook
  formula returns an upper bound above 100%.
- an **always-guess baseline**, and whether the interval clears it. If two thirds
  of clauses in a type are boilerplate then 66% accuracy is a model that has
  learned to say boilerplate.
- a **per-category breakdown** and the confusion pairs.
- a **combined figure across all three types**, because forty labels split three
  ways is thirteen per type and thirteen comparisons carries an interval about
  thirty points wide.
- a **ready-to-paste SCOPE.md row** in the honest form: figure, interval, n.

To produce the number:

    python tools/build_queue.py --type employment --n 60
    python tools/build_queue.py --type rental --n 60
    python tools/build_queue.py --type loan --n 60
    python tools/labeler.py --type employment        # then rental, then loan
    python tools/evaluate.py --classification-all

Label the `split=test` rows. Those are the blind ones, `prelabel.py` never touches
them, and `classification()` counts only labels written without assistance. That
guard was already correct.

## Still not done

**Category accuracy is still unmeasured, but is now one labelling session away.**
The harness is fixed and improved, the provider bug is gone, and the queue builder
already exists. What is left is the part that cannot be automated without making the
number circular: about forty clauses labelled by hand.

**Recall is still unmeasured and looks like the weaker half.** On documents that
deliberately planted flaggable clauses, roughly a quarter were found. Registration and
stamp duty, entry and inspection, acceleration, cross-default and KFS or APR
disclosure all went unflagged in categories the tool says it checks.

**Two tiering questions worth settling.** R-LOAN-10 treats prepayment charges as no
law on point, and there is no rule at all for KFS or APR disclosure. Both look like
regulatory-tier territory. Check the circular text, add it to the corpus, and let
retrieval verify before writing either rule.
