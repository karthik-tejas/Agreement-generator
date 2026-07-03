"""Agreement Generator — core document processing package.

Modules:
    models: Shared dataclasses used across the pipeline.
    extraction: Parses .docx files into ordered heading/body/table sections.
    matching: Maps TOR sections onto Agreement Template sections.
    generation: Populates the Agreement Template while preserving formatting.
    compliance: Heuristic FCRA (India) compliance checks on the generated agreement.
"""
