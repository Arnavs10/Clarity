"""
Proves the MCP server's tools work without needing a full MCP client session.

    python tools/test_mcp.py

Calls the same five functions the server exposes, directly, so you can see them
return real data in a couple of seconds instead of configuring Claude Desktop
first. `python mcp_server/server.py` on its own prints nothing and appears to
hang, which is correct: it is a stdio server waiting for a client, not stuck.
Ctrl+C exits it.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import importlib.util
spec = importlib.util.spec_from_file_location("srv", ROOT / "mcp_server" / "server.py")
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)


def call(tool):
    return tool.fn if hasattr(tool, "fn") else tool


print("1. lookup_statute(ica_1872, 27)")
print("  ", json.loads(call(srv.lookup_statute)("ica_1872", "27"))["text"][:90], "...\n")

print("2. search_statutes('restraint of trade')")
hits = json.loads(call(srv.search_statutes)("restraint of trade"))["hits"]
print(f"   {len(hits)} hits\n")

print("3. list_rules(employment)")
print("  ", json.loads(call(srv.list_rules)("employment"))["count"], "rules\n")

print("4. required_parameters(rental)")
print("  ", len(json.loads(call(srv.required_parameters)("rental"))["parameters"]), "fields\n")

print("5. evaluate_clause(rental, deposit_months=6)")
r = json.loads(call(srv.evaluate_clause)("rental",
    json.dumps({"deposit_months": 6, "premises_type": "residential"})))
for f in r["findings"]:
    print("  ", f["rule_id"], f["severity"], "cited" if f["citation_span"] else "no citation")

print("\nall five tools returned real data. the server works the same way when a")
print("real MCP client connects, it just does not print anything on its own.")
