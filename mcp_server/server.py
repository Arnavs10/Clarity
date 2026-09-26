"""
MCP server for Clarity.

    pip install "mcp[cli]"
    python mcp_server/server.py

Built on the official Python SDK, current spec. Five tools, and they
are the same callables the agent uses internally, so nothing here is a separate
code path that can drift from what was tested.

Point any MCP client at it. In Claude Desktop, add to claude_desktop_config.json:

    {"mcpServers": {"clause-auditor": {
        "command": "python",
        "args": ["/absolute/path/to/clause-auditor/mcp_server/server.py"]}}}
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# MCP SDK 2.x renamed FastMCP to MCPServer. Import the current name first and fall
# back to the old one, so this runs on either without editing.
try:
    from mcp.server.mcpserver import MCPServer as _Server   # SDK 2.x
except ImportError:                                          # pragma: no cover
    from mcp.server.fastmcp import FastMCP as _Server        # SDK 1.x

from rules import RuleEngine             # noqa: E402
from retrieve import StatuteIndex        # noqa: E402
from typeregistry import TypeRegistry    # noqa: E402

app = _Server("clarity")

_engine = RuleEngine()
_registry = TypeRegistry()
_index = StatuteIndex()
if not _index.chunks:
    _index.build()


@app.tool()
def lookup_statute(corpus_id: str, section: str) -> str:
    """
    Fetch the verbatim text of one numbered section of an Indian bare Act.

    corpus_id: short id such as ica_1872, copyright_1957, reg_1908, sra_1963
    section:   section number, for example "27"

    Returns the passage exactly as it appears in the Act, or a not-found message.
    Never paraphrases: the point is to be checkable against the government PDF.
    """
    as_hit = _index.by_section(_index.resolve_id(corpus_id) or corpus_id, section)
    if not as_hit:
        return json.dumps({"found": False,
                           "reason": f"section {section} not located in {corpus_id}"})
    return json.dumps({"found": True, "corpus_id": as_hit["corpus_id"],
                       "section": as_hit["section"], "text": as_hit["text"]})


@app.tool()
def search_statutes(query: str, k: int = 3) -> str:
    """
    Free-text search across the indexed statutes when no section number is known.

    Use this only after lookup_statute has failed or when the clause raises
    something the rules did not anticipate. Returns ranked passages with scores.
    """
    return json.dumps({"hits": _index.by_query(query, k=k)})


@app.tool()
def list_rules(doc_type: str) -> str:
    """
    Every risk rule for a document type, with its basis tier.

    doc_type: employment or rental

    Basis tiers: statutory means the Act says it, policy means official but
    non-binding guidance, norm means a percentile computed from the local corpus.
    """
    as_rules = _engine.rules_for(doc_type)
    if not as_rules:
        return json.dumps({"error": f"unknown doc_type. known: {_engine.doc_types()}"})
    return json.dumps({"doc_type": doc_type, "count": len(as_rules), "rules": [
        {"id": r["id"], "category": r["category"], "basis": r["basis"],
         "severity": r.get("severity"), "source": r.get("source"),
         "message": " ".join(r.get("message", "").split())} for r in as_rules]})


@app.tool()
def required_parameters(doc_type: str) -> str:
    """
    The exact parameter names the rules for this document type can read.

    Call this before extract-and-evaluate so you know what facts to pull out of a
    clause. Any parameter not in this list is ignored by the engine.
    """
    return json.dumps({"doc_type": doc_type,
                       "parameters": _engine.required_params(doc_type)})


@app.tool()
def evaluate_clause(doc_type: str, parameters: str) -> str:
    """
    Run the rule engine over parameters already extracted from a clause, and attach
    a statutory citation to every finding that has a source.

    doc_type:   employment or rental
    parameters: JSON object, for example {"deposit_months": 6, "premises_type": "residential"}

    This tool decides risk. It does not read prose and it does not guess: a rule
    fires only when its condition is met by the parameters given. A finding whose
    citation cannot be retrieved comes back with citation_span null so the caller
    can withhold it.
    """
    try:
        as_params = json.loads(parameters) if isinstance(parameters, str) else parameters
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"parameters must be valid JSON: {e}"})

    out = []
    for f in _engine.evaluate(doc_type, as_params):
        # Same span the agent and the API would resolve. Without the anchors
        # the MCP tool layer could cite a passage the web path would reject.
        as_hit = (_index.cite(f.source, anchors=f.anchors)
                  if f.source else None)
        if as_hit:
            f.citation_source = as_hit["corpus_id"]
            f.citation_span = as_hit["text"]
        out.append(f.to_dict())

    return json.dumps({"doc_type": doc_type, "findings": out,
                       "disclaimer": "Flags for review, not legal advice."})


if __name__ == "__main__":
    app.run()
