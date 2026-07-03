# Agreement Generator

A Streamlit web app that generates a completed Agreement from a Terms of Reference (TOR) document and an Agreement Template, both in Word (`.docx`) format. It semantically maps TOR sections onto the matching Agreement sections, preserves the template's original formatting, and reports the changes it made alongside an automated FCRA (India) compliance check.

## Features

- Upload a **TOR** and an **Agreement Template** (`.docx`) side by side.
- **Generate Agreement** stays disabled until both documents are uploaded.
- Semantic section mapping (not exact string matching):
  1. A curated alias table for common TOR/agreement concepts (e.g. Deliverables → Deliverables & Timelines, Scope of Work → Scope of Services).
  2. Local embedding-based semantic similarity for headings that aren't covered by the alias table.
  3. A fuzzy-text fallback if the embedding model can't be loaded (e.g. no network on first run).
- Generated Agreement preserves the template's headings, numbering, tables, and formatting by manipulating the underlying Word XML rather than re-typing text.
- **Download Agreement** as an editable `.docx` once generation completes.
- **Changes Made** panel — for every populated section: the Agreement section name, the source TOR section, and a brief summary of what was inserted.
- **FCRA Compliance Check** panel — heuristic check against common FCRA (India) contractual safeguards, with a green/red status and, when non-compliant, a list of issues, reasons, and recommended fixes.
- Step-by-step progress indicators during extraction, mapping, generation, and compliance checking, with actionable error messages if parsing fails.
- Upload new documents and regenerate at any time without refreshing the page (**Start Over** button resets state).

## Requirements

- Python 3.10+

## Setup

```bash
cd ~/Projects/agreement-generator
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
streamlit run app.py
```

Open the URL shown in the terminal (usually `http://localhost:8501`).

## Usage

1. Upload the **TOR** document and the **Agreement Template** (both `.docx`).
2. Click **Generate Agreement** once both are uploaded.
3. Watch the progress indicator move through extraction, mapping, generation, and compliance checking.
4. Download the completed agreement with **Download Agreement**.
5. Review what changed in the **Changes Made** panel and check the **FCRA Compliance Check** panel for any issues to fix.
6. Click **Start Over** to process a new pair of documents without reloading the page.

## Project structure

```
agreement-generator/
├── app.py                        # Streamlit UI — wires the modules together
├── agreement_generator/
│   ├── models.py                  # Shared dataclasses
│   ├── extraction.py              # Parses .docx into ordered heading/body/table sections
│   ├── matching.py                 # Alias + semantic + fuzzy section mapping engine
│   ├── generation.py              # Populates the template, preserving formatting
│   └── compliance.py              # FCRA (India) heuristic rule engine
├── requirements.txt
└── README.md
```

## Notes

- The FCRA compliance check is an automated heuristic aid covering common contractual safeguards under the Foreign Contribution (Regulation) Act, 2010 (as amended in 2020). It is **not legal advice** — always have a qualified professional review agreements involving foreign contribution.
- The first run downloads a small local embedding model (cached afterward) used for semantic heading matching. If no network is available, the app automatically falls back to fuzzy text matching.
- Section mapping is heuristic; sections with no confident match are left unchanged in the output and are not listed in the Changes Made panel.
