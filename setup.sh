#!/usr/bin/env bash
# Phase 0 setup. Run this once from inside the clause-auditor folder:
#
#     bash setup.sh
#
# Installs dependencies, downloads the statutes, downloads real contracts from SEC
# EDGAR for both document types, and splits everything into clauses. When it
# finishes you go straight to labeling. Safe to re-run, it skips what is there.

set -e

echo ""
echo "==> 1/5  installing dependencies"
pip install -r requirements.txt --quiet

echo ""
echo "==> 2/5  downloading statutes from India Code"
python tools/fetch_corpus.py || echo "    some statutes failed, grab those URLs in a browser"

echo ""
echo "==> 3/5  downloading real contracts from SEC EDGAR"
python tools/fetch_documents.py --type employment --n 20
python tools/fetch_documents.py --type rental --n 20
python tools/fetch_documents.py --type loan --n 20

echo ""
echo "==> 4/5  splitting documents into clauses"
as_total=0
for as_type in employment rental loan; do
  if ls data/raw/${as_type}_*.txt >/dev/null 2>&1; then
    python src/segment.py --all "$as_type"
    as_total=$((as_total + 1))
  fi
done

if [ "$as_total" -eq 0 ]; then
  echo "    nothing in data/raw/ to split, check step 3 above"
  echo "    or use the CUAD fallback in docs/SOURCES.md"
  exit 1
fi

echo ""
echo "==> 5/5  building deduplicated labeling queues"
for as_type in employment rental loan; do
  python tools/build_queue.py --type "$as_type" --n 150 || true
done

echo ""
echo "done. queues written to data/queue/"
echo ""
echo "next, start labeling:"
echo "    streamlit run tools/labeler.py"
echo ""
