"""
Core data structures. Everything downstream reads and writes these, so keep
changes here deliberate.

Clause  -> a segmented chunk of a contract
Label   -> the hand-written answer key for one clause
Finding -> what the agent produces for one clause, from Phase 2 onward
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional
import hashlib
import json

SEVERITIES = ("none", "low", "medium", "high")


@dataclass
class Clause:
    clause_id: str
    doc_id: str
    doc_type: str          # matches a slug in config/types/
    text: str
    order: int             # position in the document, 0-indexed
    heading: Optional[str] = None
    char_start: int = 0    # offset into the raw document text
    char_end: int = 0

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d):
        return Clause(**d)


@dataclass
class Label:
    """
    The answer key for one clause. Written by hand, never by a model.
    This is what precision and recall are measured against, and the
    risky flag doubles as training data for the Phase 4 triage router.
    """
    clause_id: str
    doc_id: str
    doc_type: str
    category: str          # must exist in the type profile taxonomy
    risky: bool
    severity: str          # one of SEVERITIES
    rationale: str = ""    # one line, why you called it that way
    grounding_hint: str = ""   # e.g. "MTA 2021 deposit cap" - optional
    labeler: str = "arnav"
    labeled_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    # provenance. assisted means a suggestion was shown; corrected means you changed it.
    # Only labels with assisted=False belong in the reported eval numbers.
    assisted: bool = False
    corrected: bool = False
    split: str = "test"        # "dev" (assisted) or "test" (blind)

    def validate(self, as_profile):
        """Check this label against a loaded type profile. Returns a list of problems."""
        problems = []

        valid_cats = {c["id"] for c in as_profile["taxonomy"]}
        if self.category not in valid_cats:
            problems.append(f"unknown category '{self.category}' for type {self.doc_type}")

        if self.severity not in SEVERITIES:
            problems.append(f"bad severity '{self.severity}'")

        # these two have to agree or the eval numbers become meaningless
        if self.risky and self.severity == "none":
            problems.append("marked risky but severity is none")
        if not self.risky and self.severity != "none":
            problems.append(f"marked not risky but severity is {self.severity}")

        if self.risky and not self.rationale.strip():
            problems.append("risky clauses need a rationale")

        return problems

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d):
        return Label(**d)


@dataclass
class Finding:
    """
    What the agent outputs for one clause. Not used until Phase 2, but the
    shape is fixed now so the eval harness has something stable to compare
    labels against.
    """
    clause_id: str
    doc_id: str
    doc_type: str
    category: str
    risky: bool
    severity: str
    explanation: str = ""
    citation_source: str = ""   # corpus id the claim rests on
    citation_span: str = ""     # the exact retrieved text, verbatim
    confidence: float = 0.0
    verified: bool = False      # set by the Phase 3 verification node

    def to_dict(self):
        return asdict(self)


def make_clause_id(as_doc_id, order, text):
    """Stable id, so re-running segmentation does not orphan existing labels."""
    a_digest = hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:8]
    return f"{as_doc_id}::{order:03d}::{a_digest}"


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r.to_dict() if hasattr(r, "to_dict") else r, ensure_ascii=False) + "\n")


def read_jsonl(path):
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out
