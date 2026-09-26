# Demo video script

Ninety seconds. Screen recording, no face needed. Record `streamlit run app.py`
alongside a terminal.

---

**0:00 to 0:12 — the problem**

> "You get an offer letter. Eight pages of legal language. You skim it, you sign it.
> Six months later you find out you can't leave without paying a bond."

Show a real offer letter scrolling past. Fast.

**0:12 to 0:30 — the demo**

Upload it. Let the progress bar run.

> "Clarity reads it clause by clause and tells you what to push back on."

Findings appear. Do not narrate every one. Land on the non-compete.

**0:30 to 0:50 — the differentiator**

Click "show the law this rests on". The statute text expands.

> "Every finding shows the exact passage of the Act it relies on. This is section 27
> of the Indian Contract Act, pulled from the government's own text, not written by
> a model. If a citation can't be verified, the finding is withheld rather than shown."

Hold on the expanded span for a beat. **This is the shot that sells the project.**

**0:50 to 1:10 — the architecture**

Cut to the terminal, run `python src/agent.py --demo employment --trace`.

> "A model reads the clause and pulls out facts. A rule layer applies the threshold.
> A verification node checks the citation actually supports the claim, and retries
> if it doesn't. The model never decides risk on its own."

Let the trace lines scroll.

**1:10 to 1:25 — the number**

Run `python src/router.py --benchmark employment`.

> "A small classifier filters boilerplate before anything expensive runs. Sixty-four
> percent of clauses never reach a language model, at full recall on the risky ones."

**1:25 to 1:30 — close**

Back to the UI, negotiation draft visible.

> "Then it drafts the message. You send it. Link in the description."

---

## Recording notes

- 1080p minimum, the statute text must be readable
- No music. The pauses do the work
- Zoom in on the expanded citation. If one frame gets screenshotted, that is the one
- Say "flags for review, not legal advice" once, at the end
