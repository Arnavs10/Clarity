"""
The rule engine.

Takes the parameters an LLM extracted from a clause and decides, deterministically,
whether any rule fires. The model reads the prose; this decides the policy. Keeping
those two apart is the whole point: a threshold you can print, argue with, and edit
without retraining anything.

Every finding carries the basis tier it came from, so the UI can say "section 27
says this" and "this is above the 90th percentile of your corpus" in different
voices rather than flattening both into "risky".

    from rules import RuleEngine
    as_engine = RuleEngine()
    findings = as_engine.evaluate("employment", {"post_termination_restraint": True})
"""

from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
RULES_DIR = ROOT / "config" / "rules"
NORMS_PATH = ROOT / "data" / "norms.json"

BASIS_TIERS = ("statutory", "regulatory", "policy", "norm")
SEVERITY_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}


class Finding:
    """One rule firing on one clause."""

    def __init__(self, rule, as_params, severity=None):
        self.rule_id = rule["id"]
        self.category = rule["category"]
        self.basis = rule["basis"]
        self.source = rule.get("source")
        self.metric = rule.get("metric")
        # Phrases the retrieved passage must contain for this finding to verify.
        # Without these the verifier can only check where a span opens.
        self.anchors = rule.get("anchors") or []
        self.severity = severity or rule.get("severity", "medium")
        self.risky = rule.get("risky", self.severity != "none")
        self.message = " ".join(rule.get("message", "").split())
        self.note = " ".join(rule.get("note", "").split()) or None
        self.params = as_params

        # filled in by the retriever at Phase 2b, verified at Phase 3
        self.citation_source = None
        self.citation_span = None
        self.verified = False

    def to_dict(self):
        return {
            "rule_id": self.rule_id,
            "category": self.category,
            "basis": self.basis,
            "source": self.source,
            "anchors": self.anchors,
            "metric": self.metric,
            "severity": self.severity,
            "risky": self.risky,
            "message": self.message,
            "note": self.note,
            "citation_source": self.citation_source,
            "citation_span": self.citation_span,
            "verified": self.verified,
        }

    def __repr__(self):
        return f"<{self.rule_id} {self.basis}/{self.severity}>"


def _compare(value, as_cond, norms, metric, present=True):
    """
    One condition against one extracted value.

    Conditions are either a literal to match, or a dict with one operator:
    gt, gte, lt, lte, or gt_percentile. The percentile form reads the threshold
    from data/norms.json, which tools/compute_norms.py derives from the user's
    own corpus, so no number in this file was chosen by hand.
    """
    # "absent" asks whether the clause is silent on something, which is a different
    # question from whether it says no. An IP assignment that never mentions prior
    # work sweeps it in, so silence there is the risk rather than the safe case.
    # Every other operator still treats a missing value as no match.
    if isinstance(as_cond, dict) and "absent" in as_cond:
        return (value is None or not present) == bool(as_cond["absent"])

    if value is None:
        return False

    if not isinstance(as_cond, dict):
        return value == as_cond

    for op, want in as_cond.items():
        if op == "gt":
            if not (isinstance(value, (int, float)) and value > want):
                return False
        elif op == "gte":
            if not (isinstance(value, (int, float)) and value >= want):
                return False
        elif op == "lt":
            if not (isinstance(value, (int, float)) and value < want):
                return False
        elif op == "lte":
            if not (isinstance(value, (int, float)) and value <= want):
                return False
        elif op == "gt_percentile":
            a_thresh = (norms.get(metric) or {}).get(f"p{want}")
            if a_thresh is None or not isinstance(value, (int, float)):
                return False        # no corpus evidence yet, so do not fire
            if not value > a_thresh:
                return False
        elif op == "absent":
            pass                      # handled above
        else:
            raise ValueError(f"unknown operator '{op}'")
    return True


def _matches(as_when, as_params, norms, metric):
    """Evaluate a when block. Supports all/any nesting and bare field maps."""
    if "all" in as_when:
        return all(_matches(c, as_params, norms, metric) for c in as_when["all"])
    if "any" in as_when:
        return any(_matches(c, as_params, norms, metric) for c in as_when["any"])

    return all(
        _compare(as_params.get(field), cond, norms, metric,
                 present=field in as_params)
        for field, cond in as_when.items()
    )


def _walk_fields(a_block, as_into):
    """Collect every field name a when-block reads, through all/any nesting."""
    if "all" in a_block or "any" in a_block:
        for c in a_block.get("all", []) + a_block.get("any", []):
            _walk_fields(c, as_into)
        return
    as_into.update(a_block.keys())


class RuleEngine:
    def __init__(self, rules_dir=None, norms_path=None):
        self.dir = Path(rules_dir) if rules_dir else RULES_DIR
        self.norms = {}

        a_norms = Path(norms_path) if norms_path else NORMS_PATH
        if a_norms.exists():
            import json
            self.norms = json.loads(a_norms.read_text())

        self._rules = {}
        self._load()

    def _load(self):
        if not self.dir.exists():
            raise FileNotFoundError(f"no rules dir at {self.dir}")

        for a_path in sorted(self.dir.glob("*.yaml")):
            as_doc = yaml.safe_load(a_path.read_text())
            doc_type = as_doc["doc_type"]

            seen = set()
            for r in as_doc["rules"]:
                if r["id"] in seen:
                    raise ValueError(f"duplicate rule id {r['id']} in {a_path.name}")
                seen.add(r["id"])

                if r["basis"] not in BASIS_TIERS:
                    raise ValueError(f"{r['id']}: unknown basis '{r['basis']}'")
                if r["basis"] in ("statutory", "regulatory", "policy") and "source" not in r:
                    raise ValueError(f"{r['id']}: {r['basis']} rule needs a source")
                if r["basis"] == "norm" and "metric" not in r:
                    raise ValueError(f"{r['id']}: norm rule needs a metric")

                # A rule that reads only document-scope facts is true of every
                # clause at once, so it would fire on all of them and the reader
                # would see the same finding twenty times. Every doc__ rule must
                # also be anchored to something in the clause in front of it, so
                # the finding lands where a person can go and look at it.
                as_fields = set()
                _walk_fields(r["when"], as_fields)
                as_docfields = {f for f in as_fields if f.startswith("doc__")}
                if as_docfields and as_docfields == as_fields:
                    raise ValueError(
                        f"{r['id']}: reads only document-scope fields "
                        f"{sorted(as_docfields)}. Add a clause-level condition, or "
                        f"this fires on every clause of the document.")

            self._rules[doc_type] = as_doc["rules"]

    def doc_types(self):
        return sorted(self._rules)

    def rules_for(self, doc_type):
        return self._rules.get(doc_type, [])

    def evaluate(self, doc_type, as_params):
        """Every rule that fires for these parameters, worst severity first."""
        out = []

        for rule in self.rules_for(doc_type):
            metric = rule.get("metric")
            if not _matches(rule["when"], as_params, self.norms, metric):
                continue

            severity = rule.get("severity", "medium")
            esc = rule.get("escalate_to_high_when")
            a_escalated = bool(esc) and _matches(esc, as_params, self.norms, metric)
            if a_escalated:
                severity = "high"

            severity = self._cap(rule, severity, a_escalated)
            out.append(Finding(rule, as_params, severity))

        out.sort(key=lambda f: -SEVERITY_ORDER.get(f.severity, 0))
        return out

    def params_for(self, rule_id, doc_type):
        """The fields one rule reads. Lets a caller ask which rules a given fact drives."""
        as_fields = set()

        def walk(block):
            if "all" in block or "any" in block:
                for c in block.get("all", []) + block.get("any", []):
                    walk(c)
                return
            as_fields.update(block.keys())

        for rule in self.rules_for(doc_type):
            if rule["id"] == rule_id:
                walk(rule["when"])
                break
        return sorted(as_fields)

    @staticmethod
    def _cap(rule, a_severity, a_escalated):
        """
        A norm-tier rule reaches high only by escalating into it.

        High severity is the strongest thing this tool says. On the norm tier there
        is no statute, no regulator and, since compute_norms returned nothing, no
        measured corpus behind it: the number is a drafting judgement written by
        one person. Three such judgements were sitting at high while a five percent
        prepayment fee, which costs real money and rests on a written rule, sat at
        medium. That ordering is not defensible to a reader who checks it.

        So a norm rule declares the ordinary case, and says in its own YAML what
        makes the case severe. A rule that wants high without naming that condition
        is asserting severity it cannot show, and is held at medium.

        Grounded tiers are untouched. Their severity is backed by a passage the
        reader can open.
        """
        if rule.get("basis") != "norm":
            return a_severity
        if a_severity == "high" and not a_escalated:
            return "medium"
        return a_severity

    def required_params(self, doc_type):
        """Every field the rules can read. This is what the extractor must produce."""
        as_fields = set()

        def walk(block):
            if "all" in block or "any" in block:
                for c in block.get("all", []) + block.get("any", []):
                    walk(c)
                return
            as_fields.update(block.keys())

        for rule in self.rules_for(doc_type):
            walk(rule["when"])
            if rule.get("escalate_to_high_when"):
                walk(rule["escalate_to_high_when"])

        return sorted(as_fields)


if __name__ == "__main__":
    as_engine = RuleEngine()
    for t in as_engine.doc_types():
        print(f"\n{t}: {len(as_engine.rules_for(t))} rules")
        print(f"  parameters the extractor must supply ({len(as_engine.required_params(t))}):")
        for f in as_engine.required_params(t):
            print(f"    - {f}")
