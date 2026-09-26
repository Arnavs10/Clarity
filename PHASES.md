# Phases

| Phase | Contents | Status |
|---|---|---|
| 0 | Scope lock, type registry, statute corpus, 40 real contracts from EDGAR | done |
| 1 | Segmentation, dedup by clause overlap, stratified queue | done |
| 2 | 28 tiered rules, rule engine, statute retrieval, extraction, audit pipeline | done |
| 3 | MCP server, LangGraph agent loop, verification node with bounded retry | done |
| 4 | Triage router, negotiation drafting | done |
| 5 | Evaluation harness: groundedness, rejection rate, classification | done |
| 6 | Streamlit interface | done |
| 7 | FastAPI backend, web app, browser extension | done |
| 8 | Third document type (loan) with the regulatory basis tier | done |
| 9 | Cross-draft version comparison | done |
| 10 | Diagnostics, corpus scan, index freshness | done |
| 11 | Matched fixtures and self-test for all three document types | done |
| 10 | Pipeline diagnostics, self-healing statute index | done |

## What still needs you

**About 45 minutes, and it is optional.**

Three of the four metrics need no labels at all: groundedness, the verification
self-test, and the router numbers. Only category accuracy does.

1. Label ~40 clauses in `tools/labeler.py`, blind, no suggestions. That produces the
   classification accuracy number. Skip it and the README says "not measured", which
   is honest and costs you little.
2. Run `tools/compute_norms.py` once per type to activate the norm-tier rules.
   Until then those rules stay silent rather than firing on a guessed threshold,
   which is the correct failure mode.

## Order to run everything

```bash
pip install -r requirements.txt

# 1. works offline, confirms the pipeline
python src/audit.py --demo employment
python src/agent.py --demo employment --trace

# 2. corpus and documents, if not already on disk
python tools/fetch_corpus.py
python tools/fetch_documents.py --type employment --n 20
python tools/fetch_documents.py --type rental --n 20
python tools/fetch_documents.py --type loan --n 20
python src/segment.py --all employment
python src/segment.py --all rental
python src/segment.py --all loan
python tools/build_queue.py --type employment --n 150
python tools/build_queue.py --type rental --n 150
python tools/build_queue.py --type loan --n 150

# 3. the router and its number
python src/router.py --train employment
python src/router.py --benchmark employment

# 4. the evaluation numbers
python tools/evaluate.py --groundedness employment
python tools/evaluate.py --rejection employment

# 5. with a model provider (gemini is free, no card)
export LLM_PROVIDER=gemini
export GEMINI_API_KEY=...
python src/llm.py
python tools/compute_norms.py --type employment
python src/agent.py data/raw/employment_01.txt employment --limit 10

# 6. the interface
streamlit run app.py

# 7. the MCP server
python mcp_server/server.py
```
