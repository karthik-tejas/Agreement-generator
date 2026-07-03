"""Agreement Generator — Streamlit application.

Uploads a TOR and an Agreement Template (.docx), maps TOR sections onto the
Agreement Template using semantic/alias/fuzzy matching, populates the
template while preserving its formatting, and reports the changes made
alongside an automated FCRA (India) compliance check.
"""

from __future__ import annotations

import logging

import streamlit as st

from agreement_generator import compliance, generation, matching
from agreement_generator import extraction as doc_extraction
from agreement_generator.models import DocumentParseError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def _build_custom_css(is_dark: bool) -> str:
    """Build the app's custom CSS, adapted to the active Streamlit theme.

    Streamlit already themes its own native text elements (headings,
    paragraphs, captions) correctly for both light and dark mode, so this
    intentionally avoids overriding their color with fixed hex values —
    doing so previously caused hardcoded dark-gray text to render on top of
    Streamlit's dark background, making it nearly unreadable. Only the
    colors of elements *we* render via raw HTML (title/subtitle/cards/
    captions) are set explicitly here, with a palette chosen per theme.
    """
    palette = (
        {
            "title": "#f9fafb",
            "subtitle": "#9ca3af",
            "card_bg": "#1f2937",
            "card_border": "#374151",
            "caption": "#9ca3af",
            "disclaimer": "#9ca3af",
        }
        if is_dark
        else {
            "title": "#111827",
            "subtitle": "#6b7280",
            "card_bg": "#f9fafb",
            "card_border": "#e5e7eb",
            "caption": "#6b7280",
            "disclaimer": "#9ca3af",
        }
    )
    return f"""
<style>
.block-container {{
    max-width: 960px;
    padding-top: 2.5rem;
    padding-bottom: 3rem;
}}
h1 {{ font-weight: 700; }}
.app-title {{
    text-align: center;
    font-weight: 700;
    color: {palette["title"]};
}}
.app-subtitle {{
    text-align: center;
    color: {palette["subtitle"]};
    margin-top: -0.6rem;
}}
div[data-testid="stExpander"] {{
    border: 1px solid {palette["card_border"]};
    border-radius: 10px;
    margin-bottom: 0.6rem;
}}
div[data-testid="stFileUploaderDropzone"] {{
    border-radius: 10px;
}}
.stButton > button, .stDownloadButton > button {{
    border-radius: 8px;
    font-weight: 600;
}}
.upload-card {{
    background: {palette["card_bg"]};
    border: 1px solid {palette["card_border"]};
    border-radius: 12px;
    padding: 1.1rem 1.2rem 0.6rem;
    height: 100%;
}}
.section-caption {{
    color: {palette["caption"]};
    font-size: 0.9rem;
    margin-top: -0.4rem;
}}
.disclaimer {{
    color: {palette["disclaimer"]};
    font-size: 0.8rem;
}}
@media (max-width: 640px) {{
    .block-container {{ padding-left: 1rem; padding-right: 1rem; }}
}}
</style>
"""


def _output_file_name(template_name: str) -> str:
    stem = template_name.rsplit(".", 1)[0] if "." in template_name else template_name
    return f"{stem}_Generated_Agreement.docx"


def _init_state() -> None:
    if "uploader_key" not in st.session_state:
        st.session_state.uploader_key = 0
    if "result" not in st.session_state:
        st.session_state.result = None
    if "debug_info" not in st.session_state:
        st.session_state.debug_info = None


def _reset() -> None:
    st.session_state.result = None
    st.session_state.debug_info = None
    st.session_state.uploader_key += 1


def _render_uploads() -> tuple:
    col1, col2 = st.columns(2, gap="medium")
    with col1:
        st.markdown('<div class="upload-card">', unsafe_allow_html=True)
        st.markdown("##### TOR Upload")
        st.markdown(
            '<p class="section-caption">Upload the Terms of Reference (.docx)</p>',
            unsafe_allow_html=True,
        )
        tor_file = st.file_uploader(
            "TOR document",
            type=["docx"],
            key=f"tor_upload_{st.session_state.uploader_key}",
            label_visibility="collapsed",
        )
        st.markdown("</div>", unsafe_allow_html=True)
    with col2:
        st.markdown('<div class="upload-card">', unsafe_allow_html=True)
        st.markdown("##### Agreement Template Upload")
        st.markdown(
            '<p class="section-caption">Upload the Agreement Template (.docx)</p>',
            unsafe_allow_html=True,
        )
        template_file = st.file_uploader(
            "Agreement Template document",
            type=["docx"],
            key=f"template_upload_{st.session_state.uploader_key}",
            label_visibility="collapsed",
        )
        st.markdown("</div>", unsafe_allow_html=True)
    return tor_file, template_file


def _run_pipeline(tor_file, template_file) -> None:
    st.session_state.result = None
    st.session_state.debug_info = None
    try:
        with st.status("Generating agreement…", expanded=True) as status:
            status.write("Extracting TOR sections…")
            tor_document = doc_extraction.load_document(tor_file, source_name=tor_file.name)
            tor_sections = doc_extraction.extract_sections(tor_document, source_name=tor_file.name)

            status.write("Extracting Agreement Template structure…")
            agreement_document = doc_extraction.load_document(
                template_file, source_name=template_file.name
            )
            agreement_sections = doc_extraction.extract_sections(
                agreement_document, source_name=template_file.name
            )

            st.session_state.debug_info = {
                "tor_headings": [s.heading for s in tor_sections],
                "agreement_headings": [s.heading for s in agreement_sections],
            }

            status.write("Mapping TOR sections to Agreement sections…")
            mappings, insertions, used_fallback = matching.map_sections(agreement_sections, tor_sections)

            matched_tor_headings = {t.heading for m in mappings for t in m.tor_sections} | {
                t.heading for i in insertions for t in i.tor_sections
            }
            matched_agreement_headings = {m.agreement_section.heading for m in mappings}
            unmatched_tor = [s.heading for s in tor_sections if s.heading not in matched_tor_headings]
            unmatched_agreement = [
                s.heading for s in agreement_sections if s.heading not in matched_agreement_headings
            ]

            st.session_state.debug_info.update(
                {
                    "matched_tor": matched_tor_headings,
                    "matched_agreement": matched_agreement_headings,
                    "inserted_headings": [i.heading for i in insertions],
                }
            )

            if not mappings and not insertions:
                raise DocumentParseError(
                    "No confidently matching sections were found between the TOR and the "
                    "Agreement Template. Check that both documents use clear, comparable "
                    "section headings."
                )
            if used_fallback:
                status.write(
                    "Semantic matching model unavailable — used fuzzy text matching instead."
                )

            status.write(
                f"Populating the agreement ({len(mappings)} section(s) matched, "
                f"{len(insertions)} new section(s) inserted)…"
            )
            docx_bytes, changes = generation.populate_agreement(
                agreement_document, mappings, insertions, agreement_sections
            )

            status.write("Running FCRA compliance check…")
            report = compliance.run_fcra_check(docx_bytes)

            status.update(label="Agreement generated successfully.", state="complete")

        st.session_state.result = {
            "docx_bytes": docx_bytes,
            "changes": changes,
            "report": report,
            "used_fallback": used_fallback,
            "file_name": _output_file_name(template_file.name),
            "unmatched_tor": unmatched_tor,
            "unmatched_agreement": unmatched_agreement,
        }
    except DocumentParseError as exc:
        st.error(str(exc))
    except Exception:
        logger.exception("Agreement generation failed")
        st.error(
            "Something went wrong while generating the agreement. Please verify both "
            "files are valid, non-corrupted .docx files and try again."
        )


def _render_results(result: dict) -> None:
    st.success("Agreement generated successfully.")
    if result["used_fallback"]:
        st.caption(
            "Note: the semantic matching model was unavailable, so fuzzy text matching "
            "was used instead. Section mapping may be less accurate."
        )

    st.download_button(
        "Download Agreement",
        data=result["docx_bytes"],
        file_name=result["file_name"],
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        type="primary",
        use_container_width=True,
    )

    st.divider()

    changes_col, compliance_col, unmatched_col = st.columns(3, gap="medium")

    with changes_col:
        st.subheader("Changes Made")
        changes = result["changes"]
        if not changes:
            st.write("No sections were confidently matched and populated.")
        for change in changes:
            with st.expander(change.agreement_section, expanded=False):
                st.markdown(f"**Source TOR section:** {change.tor_section}")
                st.markdown("**Summary of inserted content:**")
                st.write(change.summary)

    with compliance_col:
        st.subheader("FCRA Compliance Check")
        report = result["report"]
        if report.is_compliant:
            st.success(
                "The agreement appears to be compliant with the applicable FCRA guidelines."
            )
        else:
            st.error(
                "The agreement appears to be NON-COMPLIANT with applicable FCRA guidelines."
            )

        for issue in report.blocking_issues:
            with st.expander(f"⚠️ {issue.clause}", expanded=False):
                st.markdown(f"**Why:** {issue.reason}")
                st.markdown(f"**Recommended action:** {issue.recommendation}")

        for issue in report.advisory_issues:
            with st.expander(f"ℹ️ {issue.clause} (recommended)", expanded=False):
                st.markdown(f"**Why:** {issue.reason}")
                st.markdown(f"**Recommended action:** {issue.recommendation}")

        st.markdown(
            '<p class="disclaimer">Automated heuristic check against common FCRA '
            "contractual safeguards — not legal advice.</p>",
            unsafe_allow_html=True,
        )

    with unmatched_col:
        st.subheader("Unmatched Sections")
        unmatched_tor = result.get("unmatched_tor", [])
        unmatched_agreement = result.get("unmatched_agreement", [])

        if not unmatched_tor and not unmatched_agreement:
            st.success("Every section in both documents was matched or inserted.")
        else:
            st.caption(
                "Sections found in one document with no corresponding match in the other, "
                "so they were left untouched."
            )
            if unmatched_tor:
                with st.expander(f"TOR sections not used ({len(unmatched_tor)})", expanded=True):
                    for heading in unmatched_tor:
                        st.markdown(f"- {heading}")
            if unmatched_agreement:
                with st.expander(
                    f"Agreement sections left unchanged ({len(unmatched_agreement)})", expanded=True
                ):
                    for heading in unmatched_agreement:
                        st.markdown(f"- {heading}")


def _render_debug_panel(debug: dict) -> None:
    with st.expander("🔍 Detected sections (diagnostic)", expanded=False):
        st.caption(
            "Exactly what section headings were detected in each document, and whether "
            "each was matched, inserted, or left unused. Useful for figuring out why a "
            "particular section isn't ending up where you expect — e.g. if a heading is "
            "missing here, it wasn't recognized as a section heading during extraction "
            "(check that it uses a Word Heading style or a numbered/ALL-CAPS title)."
        )
        matched_tor = debug.get("matched_tor", set())
        matched_agreement = debug.get("matched_agreement", set())
        inserted = debug.get("inserted_headings", [])

        col1, col2 = st.columns(2, gap="medium")
        with col1:
            st.markdown("**TOR sections detected**")
            for heading in debug.get("tor_headings", []):
                marker = "✅" if heading in matched_tor else "⚪"
                note = "" if heading in matched_tor else " _(not used)_"
                st.markdown(f"{marker} {heading}{note}")
        with col2:
            st.markdown("**Agreement Template sections detected**")
            for heading in debug.get("agreement_headings", []):
                marker = "✅" if heading in matched_agreement else "⚪"
                note = "" if heading in matched_agreement else " _(no TOR match)_"
                st.markdown(f"{marker} {heading}{note}")
            for heading in inserted:
                st.markdown(f"🆕 {heading} _(inserted as new section)_")


def main() -> None:
    st.set_page_config(page_title="Agreement Generator", page_icon="📄", layout="centered")
    try:
        is_dark = st.context.theme.type == "dark"
    except Exception:
        is_dark = False
    st.markdown(_build_custom_css(is_dark), unsafe_allow_html=True)
    _init_state()

    st.markdown(
        '<h1 class="app-title">Agreement Generator</h1>'
        '<p class="app-subtitle">Upload a Terms of Reference and an Agreement Template to '
        "automatically generate a populated, downloadable agreement.</p>",
        unsafe_allow_html=True,
    )

    tor_file, template_file = _render_uploads()
    both_uploaded = tor_file is not None and template_file is not None

    st.write("")
    button_col, reset_col = st.columns([3, 1])
    with button_col:
        generate_clicked = st.button(
            "Generate Agreement",
            type="primary",
            disabled=not both_uploaded,
            use_container_width=True,
        )
    with reset_col:
        if st.button("Start Over", use_container_width=True):
            _reset()
            st.rerun()

    if not both_uploaded:
        st.info("Upload both the TOR and the Agreement Template to enable agreement generation.")

    if generate_clicked and both_uploaded:
        _run_pipeline(tor_file, template_file)

    if st.session_state.debug_info:
        _render_debug_panel(st.session_state.debug_info)

    result = st.session_state.result
    if result:
        st.divider()
        _render_results(result)


if __name__ == "__main__":
    main()
