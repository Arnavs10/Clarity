# LinkedIn post

Paste as-is. No em dashes, no AI tells. Attach the demo video, not a screenshot.

---

Most people sign contracts they have not read. I built something about that.

Clarity takes a rent agreement or an offer letter and tells you which clauses
are worth pushing back on, with the exact text of the law that makes each one a
problem.

The part I care about is how it decides.

Most tools ask a language model whether a clause looks risky. That answer cannot be
checked. When it is wrong you cannot see why, and when it is right you cannot prove it.

So I split the job. A model reads the clause and pulls out structured facts, like
"deposit is six months of rent". A separate rule layer applies the threshold. Each
rule declares where its authority comes from: statute, official guidance, or a
percentile computed from the contract corpus itself. Statutory findings carry the
retrieved passage of the bare Act. A verification step rejects any finding whose
citation does not actually open at the section the rule named.

Some numbers:

Groundedness 100 percent. Every statutory finding resolved to a real passage that
matched its cited section.

A small triage classifier routes a large share of clauses past the language model
entirely, at full recall on the risk-bearing ones, and under a millisecond of
latency. [Insert your own measured reduction percentage from tools/evaluate.py
before posting, do not reuse the number from an earlier draft of this project.]

Three rules got deleted during the build. Two rested on things the Model Tenancy Act
does not actually say, and one on a precedent I could not source. Checking the
sources properly cost a day and was the most useful day of the project.

Built with LangGraph for the agent loop, a custom MCP server for the tool layer,
scikit-learn for triage, and BM25 retrieval over bare Acts from India Code.

It flags things for review. It is not legal advice and it does not pretend to be.

Repo and demo below. Feedback welcome, especially from anyone who has read more
contracts than I have.

#AgenticAI #RAG #MachineLearning #LegalTech #BuildInPublic
