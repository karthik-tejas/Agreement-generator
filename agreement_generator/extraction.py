"""Parses .docx files into an ordered list of heading/body/table sections.

Section boundaries are detected primarily from native Word heading styles
("Heading 1", "Heading 2", …). For documents that don't use heading styles,
a small set of regex heuristics (numbered sections, "Section/Article/Clause N",
short ALL-CAPS lines) is used as a fallback so plainly-formatted templates
still work.
"""

from __future__ import annotations

import re
from typing import Any, BinaryIO

from docx import Document
from docx.document import Document as DocumentObject
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from .models import DocSection, DocumentParseError

_HEADING_STYLE_RE = re.compile(r"^heading\s*(\d+)?$", re.I)
_TITLE_STYLE_RE = re.compile(r"^title$", re.I)

# Fallback patterns for documents that don't rely on Word heading styles.
# Group 1 (if present) is used as the extracted heading text. Each pattern has
# its own max word count for the extracted title, since generic numbered
# patterns need a tighter bound to avoid mistaking numbered list content for
# headings.
_SECTION_WORD_PATTERN = re.compile(
    r"^(?:section|article|clause|part|annex(?:ure)?)\s+[\dIVXLC]+\.?\s*[:\-]?\s*(.+)$",
    re.I,
)
_NUMBERED_PATTERN = re.compile(r"^(\d+(?:\.\d+)*)[\.)]\s+(.+)$")
_ALL_CAPS_PATTERN = re.compile(r"^[A-Z][A-Z0-9\s&/\-,\.]{3,}$")

_MAX_FALLBACK_HEADING_WORDS = 12
_MAX_NUMBERED_HEADING_WORDS = 8


def _effective_bold(run) -> bool:
    """Resolve a run's bold state, falling back to its character style since
    `run.bold` is only set when formatting is applied directly on the run."""
    if run.bold is not None:
        return bool(run.bold)
    style = run.style
    if style is not None and style.font.bold is not None:
        return bool(style.font.bold)
    return False


def _is_bold_heading_candidate(paragraph: Paragraph, text: str) -> bool:
    """True for short, fully-bold standalone lines that read like a manually
    formatted heading in documents that don't use Word heading styles at all
    (a common pattern: users just bold a line instead of applying a style)."""
    if len(text.split()) > _MAX_FALLBACK_HEADING_WORDS or text.rstrip().endswith("."):
        return False
    runs = [r for r in paragraph.runs if r.text.strip()]
    if not runs:
        return False
    return all(_effective_bold(r) for r in runs)


def _is_list_item(paragraph: Paragraph) -> bool:
    """True if the paragraph is a Word bullet/numbered list item, not a heading."""
    style_name = (paragraph.style.name or "") if paragraph.style else ""
    if "list" in style_name.lower():
        return True
    p_pr = paragraph._p.pPr  # noqa: SLF001 - python-docx has no public accessor
    if p_pr is not None and p_pr.numPr is not None:
        return True
    return False


def _classify_paragraph(paragraph: Paragraph) -> tuple[bool, int, str]:
    """Return (is_heading, level, heading_text) for a paragraph."""
    text = paragraph.text.strip()
    if not text:
        return False, 0, ""

    style_name = (paragraph.style.name or "").strip() if paragraph.style else ""
    if _TITLE_STYLE_RE.match(style_name):
        # Document titles are preamble, not a matchable/populatable section.
        return False, 0, ""
    style_match = _HEADING_STYLE_RE.match(style_name)
    if style_match:
        level = int(style_match.group(1)) if style_match.group(1) else 1
        return True, level, text

    if _is_list_item(paragraph):
        return False, 0, ""

    word_count = len(text.split())
    if word_count <= _MAX_FALLBACK_HEADING_WORDS:
        match = _SECTION_WORD_PATTERN.match(text)
        if match:
            heading_text = match.group(1).strip()
            if heading_text:
                return True, 1, heading_text

        if word_count <= _MAX_NUMBERED_HEADING_WORDS and not text.rstrip().endswith("."):
            match = _NUMBERED_PATTERN.match(text)
            if match:
                heading_text = match.group(2).strip()
                if heading_text:
                    # "3." -> level 1, "3.1" -> level 2, "3.1.2" -> level 3, …
                    level = match.group(1).count(".") + 1
                    return True, level, heading_text

        if _ALL_CAPS_PATTERN.match(text):
            return True, 1, text

        if _is_bold_heading_candidate(paragraph, text):
            return True, 1, text

    return False, 0, ""


def _table_text(table: Table) -> str:
    rows_text: list[str] = []
    for row in table.rows:
        cells_text = [cell.text.strip() for cell in row.cells]
        joined = " | ".join(c for c in cells_text if c)
        if joined:
            rows_text.append(joined)
    return "\n".join(rows_text)


def load_document(file_obj: BinaryIO, *, source_name: str = "document") -> DocumentObject:
    """Open a .docx file-like object as a python-docx `Document`.

    Raises `DocumentParseError` if the file can't be opened as a Word document.
    Callers should keep the returned `Document` around and pass it to
    `extract_sections`; the resulting `DocSection`s reference this exact
    document's XML tree, which also lets `generation.populate_agreement`
    mutate it in place.
    """
    file_obj.seek(0)
    try:
        return Document(file_obj)
    except Exception as exc:  # noqa: BLE001 - python-docx raises varied exceptions
        raise DocumentParseError(
            f"Could not open '{source_name}' as a Word document. Ensure it is a "
            "valid, non-corrupted .docx file."
        ) from exc


def extract_sections(document: DocumentObject, *, source_name: str = "document") -> list[DocSection]:
    """Walk an already-opened `Document` into an ordered list of `DocSection`.

    Section boundaries are only drawn at the document's *coarsest* detected
    heading level (e.g. "Heading 1", or top-level "1."/"2." numbering) — this
    is what gets matched against the other document. Any finer-grained
    sub-headings (e.g. "Heading 2"/"3.1 …") are **not** split into their own
    sections; they and their content are kept as part of the enclosing
    top-level section's body, so all of a section's real content (including
    its subsections) travels together when it's matched and copied over.
    Previously every heading, regardless of level, started a new top-level
    section — this silently fragmented hierarchical TORs/templates so most
    of their content never matched anything and was dropped.

    Raises `DocumentParseError` if no section headings can be detected.
    """
    # First pass: classify every paragraph/table without building sections
    # yet, so we can determine the document's coarsest heading level first.
    entries: list[tuple[Any, bool, int, str, Paragraph | None]] = []
    for child in document.element.body.iterchildren():
        tag = child.tag
        if tag == qn("w:p"):
            paragraph = Paragraph(child, document)
            is_heading, level, heading_text = _classify_paragraph(paragraph)
            entries.append((child, is_heading, level, heading_text, paragraph))
        elif tag == qn("w:tbl"):
            entries.append((child, False, 0, "", None))
        # Other elements (sectPr, etc.) are ignored for section purposes.

    heading_levels = [level for _, is_heading, level, _, _ in entries if is_heading]
    if not heading_levels:
        raise DocumentParseError(
            f"Could not detect any section headings in '{source_name}'. Use Word "
            "heading styles (Heading 1/2/…) or numbered section titles "
            "(e.g. '1. Scope of Work')."
        )
    top_level = min(heading_levels)

    sections: list[DocSection] = []
    current: DocSection | None = None

    for child, is_heading, level, heading_text, paragraph in entries:
        if paragraph is not None:  # a w:p entry
            if is_heading and level <= top_level:
                current = DocSection(heading=heading_text, level=level, heading_element=child)
                sections.append(current)
                continue
            if current is None:
                continue
            # Sub-headings below the top level fall through to here and are
            # kept as regular body content, preserving their own formatting.
            current.body_elements.append(child)
            text = paragraph.text
            if text.strip():
                current.body_text += ("\n" if current.body_text else "") + text
        else:  # a w:tbl entry
            if current is None:
                continue
            current.body_elements.append(child)
            table_text = _table_text(Table(child, document))
            if table_text:
                current.body_text += ("\n" if current.body_text else "") + table_text

    return sections


def extract_full_text(document: DocumentObject) -> str:
    """Flatten an entire document's paragraphs and tables into plain text,
    in document order. Used for whole-document checks like FCRA compliance."""
    parts: list[str] = []
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            text = Paragraph(child, document).text
            if text.strip():
                parts.append(text)
        elif child.tag == qn("w:tbl"):
            table_text = _table_text(Table(child, document))
            if table_text:
                parts.append(table_text)
    return "\n".join(parts)
