"""Shared dataclasses used across the Agreement Generator pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

MatchMethod = Literal["alias", "semantic", "fuzzy", "none"]
IssueSeverity = Literal["non_compliant", "missing", "advisory"]
AnchorKind = Literal["agreement_section", "insertion", "start"]


class DocumentParseError(Exception):
    """Raised when a .docx file cannot be parsed into recognizable sections."""


@dataclass
class DocSection:
    """A heading and its body content (paragraphs/tables) from a parsed .docx."""

    heading: str
    level: int
    heading_element: Any
    body_elements: list[Any] = field(default_factory=list)
    body_text: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.body_text.strip()


@dataclass
class SectionMapping:
    """A proposed mapping from an Agreement Template section to one or more
    TOR sections. More than one TOR section means those sections' content is
    concatenated, in order, into the single Agreement section (e.g. merging
    TOR's "Deliverables" and "Timeline" into the Agreement's combined
    "Deliverables and Timelines" section)."""

    agreement_section: DocSection
    tor_sections: list[DocSection]
    confidence: float
    method: MatchMethod


@dataclass
class SectionInsertion:
    """A TOR section with no matching Agreement Template heading, to be
    inserted as a brand-new section rather than populated into an existing
    one. Positioned immediately after `anchor_agreement_section` (when
    `anchor_kind == "agreement_section"`), immediately after another pending
    insertion (`anchor_kind == "insertion"`, referencing its index in the
    insertions list), or at the very start of the document
    (`anchor_kind == "start"`)."""

    heading: str
    tor_sections: list[DocSection]
    anchor_kind: AnchorKind
    anchor_agreement_section: DocSection | None = None
    anchor_insertion_index: int | None = None


@dataclass
class ChangeRecord:
    """A human-readable record of one section populated during generation."""

    agreement_section: str
    tor_section: str
    summary: str


@dataclass
class ComplianceIssue:
    clause: str
    reason: str
    recommendation: str
    severity: IssueSeverity = "missing"


@dataclass
class ComplianceReport:
    is_compliant: bool
    issues: list[ComplianceIssue] = field(default_factory=list)
    checked_at: datetime = field(default_factory=datetime.now)

    @property
    def blocking_issues(self) -> list[ComplianceIssue]:
        return [i for i in self.issues if i.severity in ("non_compliant", "missing")]

    @property
    def advisory_issues(self) -> list[ComplianceIssue]:
        return [i for i in self.issues if i.severity == "advisory"]
