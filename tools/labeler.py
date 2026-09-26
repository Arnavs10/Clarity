"""
Labeling tool. This is where the answer key gets built.

    streamlit run tools/labeler.py

Reads every clause file in data/clauses/, skips anything already labeled, and
appends to data/labels/<doc_type>.jsonl as you go. Safe to close and reopen,
nothing is held in memory between saves.

Target pace is roughly 40 clauses an hour once the taxonomy is in your head.
Do not try to do all 300 in one sitting, the labels get sloppy and sloppy labels
are worse than no labels.
"""

from pathlib import Path
import json
import sys

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from schema import Label, SEVERITIES, read_jsonl   # noqa: E402
from typeregistry import TypeRegistry              # noqa: E402

CLAUSE_DIR = ROOT / "data" / "clauses"
QUEUE_DIR = ROOT / "data" / "queue"
SUGGEST_DIR = ROOT / "data" / "suggestions"
LABEL_DIR = ROOT / "data" / "labels"
LABEL_DIR.mkdir(parents=True, exist_ok=True)

st.set_page_config(page_title="Clause Labeler", layout="wide")


@st.cache_resource
def get_registry():
    return TypeRegistry()


as_reg = get_registry()


def load_clauses(doc_type):
    """
    Prefer the queue built by tools/build_queue.py: it is deduplicated and sampled
    across documents. Fall back to everything on disk in document order only if no
    queue exists yet.
    """
    a_queue = QUEUE_DIR / f"{doc_type}.jsonl"
    if a_queue.exists():
        return read_jsonl(a_queue)

    rows = []
    for p in sorted(CLAUSE_DIR.glob("*.jsonl")):
        for r in read_jsonl(p):
            if r.get("doc_type") == doc_type:
                rows.append(r)
    rows.sort(key=lambda r: (r["doc_id"], r["order"]))
    return rows


def load_suggestions(doc_type):
    """clause_id -> suggestion, for dev-split clauses only. Empty if none exist."""
    a_path = SUGGEST_DIR / f"{doc_type}.jsonl"
    if not a_path.exists():
        return {}
    return {r["clause_id"]: r for r in read_jsonl(a_path)}


def load_done(doc_type):
    a_path = LABEL_DIR / f"{doc_type}.jsonl"
    if not a_path.exists():
        return {}
    return {r["clause_id"]: r for r in read_jsonl(a_path)}


def append_label(as_label):
    a_path = LABEL_DIR / f"{as_label.doc_type}.jsonl"
    with open(a_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(as_label.to_dict(), ensure_ascii=False) + "\n")


# ---------- sidebar ----------

st.sidebar.title("Clause Labeler")
doc_type = st.sidebar.selectbox("Document type", as_reg.slugs())

as_clauses = load_clauses(doc_type)
done = load_done(doc_type)
as_suggestions = load_suggestions(doc_type)
skipped = st.session_state.setdefault("skipped", set())
pending = [c for c in as_clauses if c["clause_id"] not in done and c["clause_id"] not in skipped]

# nothing left except things you skipped, so bring them back
if not pending and skipped:
    st.session_state["skipped"] = set()
    pending = [c for c in as_clauses if c["clause_id"] not in done]

st.sidebar.metric("Labeled", f"{len(done)} / {len(as_clauses)}")
if as_clauses:
    st.sidebar.progress(len(done) / len(as_clauses))

st.sidebar.caption(f"{len(pending)} left in the queue")
st.sidebar.caption(f"{len(set(c['doc_id'] for c in as_clauses))} source documents")

n_blind = sum(1 for c in as_clauses if c.get("split", "test") == "test")
st.sidebar.caption(f"{n_blind} blind test / {len(as_clauses) - n_blind} assisted dev")

as_saved = list(done.values())
n_assisted = sum(1 for r in as_saved if r.get("assisted"))
if n_assisted:
    n_fixed = sum(1 for r in as_saved if r.get("corrected"))
    st.sidebar.metric("Correction rate", f"{n_fixed / n_assisted:.0%}",
                      help="how often you overrode the suggestion")

show_anchors = st.sidebar.checkbox("Show legal anchors", value=True)
if show_anchors:
    st.sidebar.markdown("**Anchors**")
    for a in as_reg.anchors(doc_type):
        st.sidebar.caption(f"- {a}")

# ---------- main ----------

if not as_clauses:
    st.warning(
        f"No clauses found for type '{doc_type}'.\n\n"
        "Run these first:\n\n"
        f"`python src/segment.py --all {doc_type}`\n\n"
        f"`python tools/build_queue.py --type {doc_type} --n 150`"
    )
    st.stop()

if not pending:
    st.success(f"All {len(as_clauses)} clauses labeled for {doc_type}. Run the validator next.")
    st.stop()

a_clause = pending[0]

left, right = st.columns([3, 2])

with left:
    st.caption(f"{a_clause['doc_id']}  ·  clause {a_clause['order'] + 1}  ·  {len(pending)} remaining")
    if a_clause.get("heading"):
        st.markdown(f"**{a_clause['heading']}**")
    st.text_area("Clause text", a_clause["text"], height=340, disabled=True, label_visibility="collapsed")

with right:
    as_cats = as_reg.categories(doc_type)
    watch = as_reg.watch_notes(doc_type)

    is_blind = a_clause.get("split", "test") == "test"
    a_sugg = None if is_blind else as_suggestions.get(a_clause["clause_id"])

    if is_blind:
        st.caption("Blind test clause. No suggestion shown, this one counts toward your reported numbers.")
    elif a_sugg:
        conf = a_sugg.get("confidence", 0)
        if conf < 0.6:
            st.warning(f"Suggestion is low confidence ({conf:.0%}). Read this one properly.")
        else:
            st.caption(f"Pre-annotated, confidence {conf:.0%}. Check it, change anything wrong.")

    a_default = a_sugg["category"] if a_sugg and a_sugg["category"] in as_cats else "boilerplate"
    category = st.selectbox("Category", as_cats, index=as_cats.index(a_default))
    st.caption(as_reg.category_desc(doc_type, category))
    if category in watch:
        st.info(f"Watch for: {watch[category]}")

    risky = st.toggle("Risky, worth flagging to the user",
                      value=bool(a_sugg["risky"]) if a_sugg else False)

    if risky:
        as_levels = ["low", "medium", "high"]
        a_sev = a_sugg["severity"] if a_sugg and a_sugg.get("severity") in as_levels else "medium"
        severity = st.radio("Severity", as_levels, horizontal=True, index=as_levels.index(a_sev))
    else:
        severity = "none"

    rationale = st.text_input(
        "Rationale" + (" (required)" if risky else " (optional)"),
        value=(a_sugg.get("rationale", "") if a_sugg and risky else ""),
        placeholder="one line, why you called it that way",
    )
    grounding = st.text_input("Grounding hint (optional)", placeholder="e.g. s.27 Contract Act")

    c1, c2 = st.columns(2)

    with c1:
        if st.button("Save and next", type="primary", use_container_width=True):
            changed = bool(a_sugg) and (
                category != a_sugg["category"]
                or risky != bool(a_sugg["risky"])
                or (risky and severity != a_sugg.get("severity"))
            )
            as_label = Label(
                clause_id=a_clause["clause_id"],
                doc_id=a_clause["doc_id"],
                doc_type=doc_type,
                category=category,
                risky=risky,
                severity=severity,
                rationale=rationale.strip(),
                grounding_hint=grounding.strip(),
                assisted=bool(a_sugg),
                corrected=changed,
                split=a_clause.get("split", "test"),
            )
            problems = as_label.validate(as_reg.get(doc_type))
            if problems:
                for p in problems:
                    st.error(p)
            else:
                append_label(as_label)
                st.rerun()

    with c2:
        if st.button("Skip for now", use_container_width=True):
            st.session_state["skipped"].add(a_clause["clause_id"])
            st.rerun()
