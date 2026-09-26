"""
Clarity UI.

    streamlit run app.py

Upload a contract, get findings with the statute text that supports each one.
Deliberately reads like a document rather than a dashboard: contract on the left,
findings on the right, because the reader's question is always "where in my
contract" and never "what is the aggregate trend".
"""

import json
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import segment                          # noqa: E402
from agent import ClauseAgent           # noqa: E402
from typeregistry import TypeRegistry   # noqa: E402
from negotiate import draft_offline     # noqa: E402

st.set_page_config(page_title="Clarity", layout="wide",
                   initial_sidebar_state="expanded")

st.markdown("""
<style>
  .stApp { background: #FAF9F6; }
  .cl { border-left: 3px solid #D9D4CA; padding: .55rem 0 .55rem .9rem;
        margin-bottom: .5rem; font-family: Georgia, serif; font-size: .88rem;
        line-height: 1.55; color: #2B2B2B; }
  .cl-high { border-left-color: #A63D2F; background: #FBF3F1; }
  .cl-med  { border-left-color: #B5822E; background: #FCF8F0; }
  .cl-low  { border-left-color: #7A8B6F; background: #F5F8F3; }
  .pill { display:inline-block; padding:.1rem .5rem; border-radius:3px;
          font-size:.66rem; font-weight:600; letter-spacing:.4px;
          text-transform:uppercase; margin-right:.4rem; }
  .p-high{background:#A63D2F;color:#fff} .p-med{background:#B5822E;color:#fff}
  .p-low{background:#7A8B6F;color:#fff}  .p-basis{background:#E8E4DA;color:#4A4A4A}
  .span { background:#F2EFE7; border-left:2px solid #B0A88F; padding:.5rem .75rem;
          font-family: Georgia, serif; font-size:.76rem; color:#4A4A4A;
          line-height:1.5; margin-top:.4rem; }
</style>
""", unsafe_allow_html=True)

SEV = {"high": ("p-high", "cl-high"), "medium": ("p-med", "cl-med"),
       "low": ("p-low", "cl-low")}
BASIS_LABEL = {"statutory": "the Act says this",
               "regulatory": "the regulator directs",
               "policy": "official benchmark, non-binding",
               "norm": "unusual vs comparable contracts"}


@st.cache_resource
def get_agent():
    return ClauseAgent()


@st.cache_resource
def get_registry():
    return TypeRegistry()


as_reg = get_registry()

st.sidebar.title("Clarity")
st.sidebar.caption("Know what you are signing before you sign it")
doc_type = st.sidebar.selectbox("Document type", as_reg.slugs())
as_file = st.sidebar.file_uploader("Contract", type=["txt", "pdf", "docx"])
run_it = st.sidebar.button("Audit", type="primary", use_container_width=True)

st.sidebar.divider()
st.sidebar.caption("**How risk is decided**")
st.sidebar.caption(
    "A model reads each clause and extracts facts. A rule layer applies the "
    "threshold. Every statutory finding shows the passage it relied on, and any "
    "finding whose citation cannot be verified is withheld.")
st.sidebar.divider()
st.sidebar.caption("Flags for review. Not legal advice.")

if not as_file:
    st.title("Clarity")
    st.write("Upload a rent agreement or an offer letter. Each clause is checked "
             "against a rule grounded in Indian statute, and every finding shows "
             "the text of the law it rests on.")
    c1, c2, c3 = st.columns(3)
    c1.metric("Rules", sum(len(get_agent().engine.rules_for(t)) for t in as_reg.slugs()))
    c2.metric("Statutes indexed", len(set(c["corpus_id"] for c in get_agent().index.chunks)) or 6)
    c3.metric("Document types", len(as_reg.slugs()))
    st.stop()

if run_it or st.session_state.get("done"):
    if run_it:
        # Read from memory. This wrote the upload to a temporary file that was
        # never deleted, and segment.run() then saved its clauses as well.
        as_agent = get_agent()
        as_text = segment.read_bytes(as_file.getvalue(), Path(as_file.name).suffix)
        as_clauses, _how, _n = segment.split_text(as_text, doc_type)
        as_results = []
        bar = st.progress(0.0, text="reading clauses")
        for i, c in enumerate(as_clauses, 1):
            out = as_agent.run(doc_type, c.text, clause_id=c.clause_id)
            out["heading"] = c.heading
            out["clause_text"] = c.text
            as_results.append(out)
            bar.progress(i / len(as_clauses), text=f"clause {i} of {len(as_clauses)}")
        bar.empty()
        st.session_state["results"] = as_results
        st.session_state["done"] = True

    as_results = st.session_state["results"]
    as_flagged = [r for r in as_results if r.get("findings")]
    n_high = sum(1 for r in as_flagged for f in r["findings"] if f["severity"] == "high")

    a, b, c, d = st.columns(4)
    a.metric("Clauses", len(as_results))
    b.metric("Flagged", len(as_flagged))
    c.metric("High severity", n_high)
    d.metric("Not analysed", sum(1 for r in as_results if not r.get("triage_keep")))

    left, right = st.columns([1, 1], gap="large")

    with left:
        st.subheader("Contract")
        for i, r in enumerate(as_results):
            sev = max((f["severity"] for f in r.get("findings", [])),
                      key=lambda s: {"high": 3, "medium": 2, "low": 1}.get(s, 0),
                      default=None)
            klass = SEV.get(sev, ("", ""))[1]
            st.markdown(
                f"<div class='cl {klass}'>{r['clause_text'][:600]}"
                f"{'...' if len(r['clause_text']) > 600 else ''}</div>",
                unsafe_allow_html=True)

    with right:
        st.subheader(f"Findings ({sum(len(r['findings']) for r in as_flagged)})")
        if not as_flagged:
            st.success("No clause triggered a rule.")
        for r in as_flagged:
            for f in r["findings"]:
                pill, _ = SEV.get(f["severity"], ("p-basis", ""))
                st.markdown(
                    f"<span class='pill {pill}'>{f['severity']}</span>"
                    f"<span class='pill p-basis'>{BASIS_LABEL.get(f['basis'], f['basis'])}</span>"
                    f"<br><b>{f['message']}</b>", unsafe_allow_html=True)
                if f.get("citation_span"):
                    with st.expander("show the law this rests on"):
                        st.markdown(f"<div class='span'>{f['citation_span']}</div>",
                                    unsafe_allow_html=True)
                        st.caption(f"{f['citation_source']} · rule {f['rule_id']} · verified")
                if f.get("note"):
                    st.caption(f["note"])
                st.divider()

        as_all = [f for r in as_flagged for f in r["findings"]]
        if as_all:
            st.subheader("Negotiation draft")
            st.caption("Read it, edit it, send it yourself. Nothing is sent for you.")
            as_draft = draft_offline(doc_type, as_all)
            if as_draft:
                st.text_area("Draft", as_draft, height=260, label_visibility="collapsed")
                st.download_button("Download findings as JSON",
                                   json.dumps([r for r in as_flagged], indent=2, default=str),
                                   file_name="findings.json", use_container_width=True)
