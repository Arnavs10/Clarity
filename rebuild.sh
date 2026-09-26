#!/usr/bin/env bash
# Re-download and rebuild everything with the fixed HTML stripper.
#
#     bash rebuild.sh
#
# Needed because the earlier stripper broke words across lines, so the text already
# in data/raw is damaged. Takes about 6 minutes, almost all of it downloading.
# Nothing here is labeled yet, so nothing is lost.

set -e

echo ""
echo "==> clearing damaged text"
rm -f data/raw/employment_*.txt data/raw/rental_*.txt data/raw/loan_*.txt
rm -f data/clauses/*.jsonl
rm -f data/queue/*.jsonl

echo ""
echo "==> re-downloading contracts with the fixed stripper"
python tools/fetch_documents.py --type employment --n 20
python tools/fetch_documents.py --type rental --n 20
python tools/fetch_documents.py --type loan --n 20

echo ""
echo "==> segmenting"
python src/segment.py --all employment
python src/segment.py --all rental
python src/segment.py --all loan

echo ""
echo "==> building labeling queues"
python tools/build_queue.py --type employment --n 150
python tools/build_queue.py --type rental --n 150
python tools/build_queue.py --type loan --n 150

echo ""
echo "==> sample of what you will be labeling"
python - <<'PYEOF'
import json
from pathlib import Path
for t in ("employment", "rental", "loan"):
    p = Path(f"data/queue/{t}.jsonl")
    if not p.exists():
        continue
    rows = [json.loads(l) for l in open(p)]
    print(f"\n--- {t}: {len(rows)} queued ---")
    for r in rows[:2]:
        print("  " + r["text"][:180].replace("\n", " ") + " ...")
PYEOF

echo ""
echo "text should read as clean sentences above, not broken fragments."
echo ""
echo "next:"
echo "    streamlit run tools/labeler.py"
echo ""
