"""Populates the Agreement Template with matched TOR content.

The Agreement Template's `Document` (as returned by
`extraction.load_document`) is mutated in place:

- For each `SectionMapping`, the matched section's placeholder body content
  is removed and replaced with a deep copy of the corresponding TOR
  section(s)' body elements (paragraphs and tables). When a mapping draws
  from more than one TOR section (a composite match, e.g. "Deliverables and
  Timelines"), their content is concatenated in order.
- For each `SectionInsertion` (a well-known TOR concept with no matching
  Agreement heading at all, e.g. "Objective"), a brand-new heading is
  created — cloned from a nearby Agreement heading so it inherits the
  template's own heading style/numbering — and inserted at the appropriate
  position, followed by the TOR section's content.

Because this manipulates the real OOXML tree rather than re-typing text,
the template's headings, numbering, and table structures are preserved.

Known limitation: content copied from the TOR keeps its own inline
(direct) run formatting, but paragraph/list styles and numbering that are
defined in the TOR's `styles.xml`/`numbering.xml` are not merged into the
Agreement Template, so highly-styled TOR lists may lose their visual
numbering once moved. Embedded images/objects are stripped from copied
content to avoid producing a corrupted document, since their relationship
IDs don't carry over across documents.
"""

from __future__ import annotations

import copy
import io
from typing import Any

from docx.document import Document as DocumentObject
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

from .models import ChangeRecord, DocSection, SectionInsertion, SectionMapping

_SUMMARY_MAX_CHARS = 220


def _strip_unsupported_media(element) -> None:
    """Remove embedded drawings/objects whose relationship IDs won't resolve
    in the destination document, to avoid producing a corrupted .docx."""
    for tag in (qn("w:drawing"), qn("w:object"), qn("w:pict")):
        for node in element.findall(f".//{tag}"):
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)


def _summarize(body_text: str, *, max_chars: int = _SUMMARY_MAX_CHARS) -> str:
    text = " ".join(body_text.split())
    if not text:
        return "The matched TOR section had no extractable text content."
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + "…"


def _combined_text(tor_sections) -> str:
    return "\n".join(t.body_text for t in tor_sections if t.body_text)


def _combined_heading_names(tor_sections) -> str:
    return "; ".join(t.heading for t in tor_sections)


def _insert_tor_content(document: DocumentObject, insertion_point, tor_sections) -> Any:
    """Deep-copy each TOR section's body elements after `insertion_point`,
    returning the final inserted element (or `insertion_point` if none)."""
    for tor_section in tor_sections:
        for tor_element in tor_section.body_elements:
            new_element = copy.deepcopy(tor_element)
            _strip_unsupported_media(new_element)
            insertion_point.addnext(new_element)
            insertion_point = new_element
    return insertion_point


def _clone_heading_paragraph(document: DocumentObject, style_source_element, text: str):
    """Clone a heading paragraph's formatting/style but replace its text,
    so newly inserted sections visually match the template's own headings."""
    new_p = copy.deepcopy(style_source_element)
    paragraph = Paragraph(new_p, document)
    for run in list(paragraph.runs):
        run._element.getparent().remove(run._element)  # noqa: SLF001
    for hyperlink in new_p.findall(qn("w:hyperlink")):
        new_p.remove(hyperlink)
    paragraph.add_run(text)
    return new_p


def populate_agreement(
    agreement_document: DocumentObject,
    mappings: list[SectionMapping],
    insertions: list[SectionInsertion] | None = None,
    agreement_sections: list[DocSection] | None = None,
) -> tuple[bytes, list[ChangeRecord]]:
    """Populate `agreement_document` in place with content from `mappings`
    and `insertions`.

    `agreement_sections` (the full, original list of Agreement Template
    sections, as returned by `extraction.extract_sections`) is used as a
    fallback source of heading formatting for insertions that have no
    preceding mapped section to anchor/style off of. If omitted, the first
    mapped section is used instead (fine as long as at least one mapping
    exists).

    Returns the saved document as bytes along with a `ChangeRecord` per
    populated or inserted section, describing what changed.
    """
    insertions = insertions or []
    change_records: list[ChangeRecord] = []

    # Tracks, per Agreement section (by identity), the last XML element that
    # now belongs to it after mutation — used to anchor insertions correctly.
    last_element_for_agreement: dict[int, Any] = {}
    if agreement_sections:
        fallback_heading_element = agreement_sections[0].heading_element
    elif mappings:
        fallback_heading_element = mappings[0].agreement_section.heading_element
    else:
        fallback_heading_element = None

    for mapping in mappings:
        if not mapping.tor_sections:
            continue

        agreement_section = mapping.agreement_section
        heading_element = agreement_section.heading_element

        for old_element in list(agreement_section.body_elements):
            parent = old_element.getparent()
            if parent is not None:
                parent.remove(old_element)

        final_element = _insert_tor_content(agreement_document, heading_element, mapping.tor_sections)
        last_element_for_agreement[id(agreement_section)] = final_element

        change_records.append(
            ChangeRecord(
                agreement_section=agreement_section.heading,
                tor_section=_combined_heading_names(mapping.tor_sections),
                summary=_summarize(_combined_text(mapping.tor_sections)),
            )
        )

    last_element_for_insertion: dict[int, Any] = {}
    style_source_for_insertion: dict[int, Any] = {}

    for insertion_index, insertion in enumerate(insertions):
        if not insertion.tor_sections:
            continue

        if insertion.anchor_kind == "agreement_section" and insertion.anchor_agreement_section is not None:
            anchor_section = insertion.anchor_agreement_section
            anchor_element = last_element_for_agreement.get(
                id(anchor_section), anchor_section.heading_element
            )
            style_source = anchor_section.heading_element
        elif insertion.anchor_kind == "insertion" and insertion.anchor_insertion_index is not None:
            anchor_element = last_element_for_insertion[insertion.anchor_insertion_index]
            style_source = style_source_for_insertion[insertion.anchor_insertion_index]
        else:
            anchor_element = None
            style_source = fallback_heading_element

        if style_source is None:
            # No existing heading anywhere in the template to copy formatting
            # from (an empty template); skip gracefully rather than
            # producing an unstyled/corrupt document.
            continue

        new_heading_element = _clone_heading_paragraph(agreement_document, style_source, insertion.heading)

        if anchor_element is not None:
            anchor_element.addnext(new_heading_element)
        else:
            agreement_document.element.body.insert(0, new_heading_element)

        final_element = _insert_tor_content(agreement_document, new_heading_element, insertion.tor_sections)
        last_element_for_insertion[insertion_index] = final_element
        style_source_for_insertion[insertion_index] = new_heading_element

        change_records.append(
            ChangeRecord(
                agreement_section=f"{insertion.heading} (new section)",
                tor_section=_combined_heading_names(insertion.tor_sections),
                summary=_summarize(_combined_text(insertion.tor_sections)),
            )
        )

    buffer = io.BytesIO()
    agreement_document.save(buffer)
    return buffer.getvalue(), change_records
