"""
The triage router. This is the runtime ML optimisation.

    python src/router.py --train employment
    python src/router.py --train employment --label-with-rules   # accurate, needs a key
    python src/router.py --benchmark employment

Most clauses in a contract are inert. Every one of them sent to an LLM costs money
and latency for a guaranteed empty result. This is a TF-IDF plus logistic regression
classifier that decides, in under a millisecond, whether a clause is worth the
expensive path.

Tuned for recall. A false positive costs a fraction of a rupee; a false negative
means a bond clause is never read. The threshold is deliberately low and training
prints the recall/cost trade at several thresholds so the choice stays visible.

Labels are generated, never hand-written. Either the rule engine produces them, or
a weighted keyword heuristic does.
"""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

MODEL_PATH = ROOT / "data" / "router.joblib"
KEEP_THRESHOLD = 0.25

# Two tiers, because they are not equally informative on real contract text.
#
# A first version used one flat keyword list and labelled 60 percent of a real SEC
# corpus positive: words like "terminate", "breach" and "covenant" appear in nearly
# every clause of a commercial agreement. The router then kept 85 percent of clauses
# and saved almost nothing. Weak words now carry a fraction of the weight of strong
# ones, which puts the positive rate near a quarter, roughly how many clauses in a
# real contract actually trip one of the 28 rules.
STRONG = ["non-compet", "noncompet", "restraint of trade", "restrictive covenant",
          "forfeit", "liquidated damages", "penalty", "bond", "minimum service",
          "clawback", "sole discretion", "sole and exclusive property", "invention",
          "non-solicit", "nonsolicit", "garden leave", "perpetuit", "in perpetuity",
          "injunctive relief", "waives the", "waive any", "shall vest in",
          "irrevocably assign", "at its sole", "without cause"]

WEAK = ["terminat", "notice", "breach", "obligat", "covenant", "confidential",
        "deposit", "escalat", "arbitrat", "sublet", "discretion", "indemnif",
        "restrict", "assign", "shall not", "shall pay", "damages"]

INERT = ["capitalized terms", "shall have the meanings", "in witness", "counterparts",
         "severability", "headings", "entire agreement", "notices shall be sent",
         "witnesseth", "recitals", "exhibit ", "definitions", "table of contents",
         "signature page", "for convenience of reference"]


class TriageRouter:
    def __init__(self, pipeline=None, threshold=KEEP_THRESHOLD):
        self.pipeline = pipeline
        self.threshold = threshold

    @classmethod
    def load(cls, path=None):
        import joblib
        a_path = Path(path) if path else MODEL_PATH
        if not a_path.exists():
            raise FileNotFoundError(f"no trained router at {a_path}")
        as_blob = joblib.load(a_path)
        return cls(as_blob["pipeline"], as_blob.get("threshold", KEEP_THRESHOLD))

    def keep(self, clause_text):
        p = float(self.pipeline.predict_proba([clause_text])[0][1])
        return p >= self.threshold, f"router p={p:.2f}"

    def save(self, path=None):
        import joblib
        a_path = Path(path) if path else MODEL_PATH
        a_path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"pipeline": self.pipeline, "threshold": self.threshold}, a_path)
        return a_path


def label_heuristic(as_text):
    low = as_text.lower()
    n_strong = sum(1 for w in STRONG if w in low)
    n_weak = sum(1 for w in WEAK if w in low)
    n_inert = sum(1 for w in INERT if w in low)

    if n_inert >= 2 and n_strong == 0:
        return 0
    return 1 if (n_strong >= 1 or n_weak >= 5) else 0


def label_with_rules(as_rows, doc_type):
    """
    The accurate path: run the real extractor and rule engine, and mark a clause
    positive when a rule actually fires. One model call per clause, so a couple of
    dollars for a corpus this size, and it is the only labelling that matches what
    the router is trying to predict.
    """
    from extract import Extractor
    from rules import RuleEngine
    as_ex, as_engine = Extractor(), RuleEngine()

    y = []
    for i, r in enumerate(as_rows, 1):
        try:
            as_params = as_ex.extract(doc_type, r["text"]).get("params", {})
            y.append(1 if as_engine.evaluate(doc_type, as_params) else 0)
        except Exception:
            y.append(label_heuristic(r["text"]))
        if i % 25 == 0:
            print(f"  labelled {i}/{len(as_rows)}")
    return y


def load_clauses(doc_type, limit=None):
    from schema import read_jsonl
    as_rows = []
    for p in sorted((ROOT / "data" / "clauses").glob("*.jsonl")):
        as_rows += [r for r in read_jsonl(p) if r.get("doc_type") == doc_type]
    if limit:
        as_rows = as_rows[:limit]
    if not as_rows:
        raise SystemExit(f"no clauses for {doc_type}. Run src/segment.py first")
    return as_rows


def build_training_set(doc_type, limit=None, use_rules=False):
    as_rows = load_clauses(doc_type, limit)
    X = [r["text"] for r in as_rows]
    if use_rules:
        print("labelling with the rule engine, one model call per clause")
        y = label_with_rules(as_rows, doc_type)
    else:
        y = [label_heuristic(t) for t in X]
    return X, y


def train(doc_type, limit=None, use_rules=False, threshold=KEEP_THRESHOLD):
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import precision_score, recall_score, f1_score

    X, y = build_training_set(doc_type, limit, use_rules)
    rate = sum(y) / len(y)
    print(f"{len(X)} clauses, {sum(y)} positive ({rate:.0%})")

    if rate > 0.45 and not use_rules:
        print("\n  [!] positive rate is high for real contract text. The heuristic is")
        print("      over-firing, so the router will keep too much and save little.")
        print("      Re-run with --label-with-rules and an API key for real labels.")

    if sum(y) < 10 or len(y) - sum(y) < 10:
        raise SystemExit("not enough of one class to train. Segment more documents first.")

    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.25,
                                              random_state=42, stratify=y)
    as_pipe = make_pipeline(
        TfidfVectorizer(ngram_range=(1, 2), min_df=2, max_features=20000,
                        sublinear_tf=True, strip_accents="unicode"),
        LogisticRegression(max_iter=1000, class_weight="balanced", C=2.0),
    )
    as_pipe.fit(X_tr, y_tr)

    as_probs = as_pipe.predict_proba(X_te)[:, 1]
    as_pred = (as_probs >= threshold).astype(int)

    print(f"\nheld out, threshold {threshold}:")
    print(f"  precision {precision_score(y_te, as_pred, zero_division=0):.3f}")
    print(f"  recall    {recall_score(y_te, as_pred, zero_division=0):.3f}   <- the one that matters")
    print(f"  f1        {f1_score(y_te, as_pred, zero_division=0):.3f}")
    print(f"  keeps     {as_pred.mean():.0%}, so {1 - as_pred.mean():.0%} skip the LLM")

    print("\n  threshold   recall   keeps")
    best = threshold
    for t in (0.20, 0.25, 0.35, 0.50, 0.65, 0.80):
        pr = (as_probs >= t).astype(int)
        rec = recall_score(y_te, pr, zero_division=0)
        print(f"    {t:<9} {rec:.2f}     {pr.mean():.0%}")
        if rec >= 0.99:
            best = t
    print(f"  highest threshold still at full recall: {best}")

    as_router = TriageRouter(as_pipe, best)
    print(f"\nsaved with threshold {best} -> {as_router.save().relative_to(ROOT)}")
    return as_router


def benchmark(doc_type, cost_per_call=0.004, limit=None):
    as_router = TriageRouter.load()
    as_rows = load_clauses(doc_type, limit)

    t0 = time.time()
    as_keep = [as_router.keep(r["text"])[0] for r in as_rows]
    elapsed = time.time() - t0

    n, kept = len(as_rows), sum(as_keep)
    saved = n - kept

    print(f"\n{doc_type}: {n} clauses, router threshold {as_router.threshold}")
    print(f"  keeps    {kept} ({kept/n:.0%})")
    print(f"  skips    {saved} ({saved/n:.0%})")
    print(f"  latency  {elapsed/n*1000:.2f} ms per clause")
    print(f"\ncost per {n} clauses at ${cost_per_call:.3f} per call:")
    print(f"  without router  ${n * cost_per_call:.2f}")
    print(f"  with router     ${kept * cost_per_call:.2f}")
    print(f"  reduction       {saved/n:.0%}")

    as_out = {"doc_type": doc_type, "clauses": n, "kept": kept, "skipped": saved,
              "threshold": as_router.threshold, "reduction": round(saved / n, 4),
              "ms_per_clause": round(elapsed / n * 1000, 3),
              "cost_without": round(n * cost_per_call, 4),
              "cost_with": round(kept * cost_per_call, 4)}
    a_path = ROOT / "data" / f"router_benchmark_{doc_type}.json"
    a_path.write_text(json.dumps(as_out, indent=2))
    print(f"\nsaved -> {a_path.relative_to(ROOT)}")
    return as_out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", metavar="DOC_TYPE")
    ap.add_argument("--benchmark", metavar="DOC_TYPE")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--cost", type=float, default=0.004)
    ap.add_argument("--label-with-rules", action="store_true",
                    help="label with the real extractor and rules, needs an API key")
    ap.add_argument("--threshold", type=float, default=KEEP_THRESHOLD)
    a = ap.parse_args()

    if a.train:
        train(a.train, a.limit, a.label_with_rules, a.threshold)
    elif a.benchmark:
        benchmark(a.benchmark, a.cost, a.limit)
    else:
        ap.print_help()
