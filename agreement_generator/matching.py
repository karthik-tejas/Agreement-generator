"""Maps Agreement Template sections onto their best-matching TOR sections.

Three-tier strategy, in order of precedence:

1. A curated canonical alias table resolves well-known contract concepts
   (e.g. "Deliverables" <-> "Deliverables & Timelines", "Scope of Work" <->
   "Scope of Services") with high confidence, regardless of wording.
2. A local sentence-embedding model (via chromadb's default embedding
   function, a small local ONNX MiniLM model) scores semantic similarity for
   headings the alias table doesn't cover.
3. If the embedding model can't be loaded (e.g. no network available on
   first run to fetch it), a fuzzy text-similarity fallback keeps the app
   functional in a degraded mode.

On top of that, two extra behaviors are supported:

- Composite sections: an Agreement heading that represents a combination of
  concepts (e.g. "Deliverables and Timelines") pulls in and concatenates
  content from *multiple* TOR sections (Deliverables + Timeline).
- New-section insertion: a small set of well-known TOR concepts (Objective,
  Payment Terms) are inserted as brand-new sections when the Agreement
  Template doesn't already have a corresponding heading, positioned right
  after wherever the nearest preceding TOR section landed.

Matches are resolved with a greedy one-to-one assignment so each TOR section
is only used once (except when explicitly combined via `COMPOSITE_SECTIONS`).
"""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher

from .models import DocSection, SectionInsertion, SectionMapping

logger = logging.getLogger(__name__)

MATCH_THRESHOLD = 0.45
_BODY_SNIPPET_WORDS = 40
_ALIAS_SCORE = 0.95
_SEMANTIC_WEIGHT = 0.75
_FUZZY_WEIGHT = 0.25

# Canonical contract concepts and their common phrasings. Extend this table
# to teach the matcher new section synonyms without touching any other code.
CANONICAL_SECTIONS: dict[str, list[str]] = {
    "background": [
        "background",
        "project background",
        "background and context",
        "background of the assignment",
        "introduction",
    ],
    "objectives": [
        "objective",
        "objectives",
        "project objectives",
        "aim and objectives",
        "goal",
        "goals",
    ],
    "scope_of_work": [
        "scope of work",
        "scope of services",
        "scope of the assignment",
        "project scope",
        "scope",
        "objectives and scope",
    ],
    "deliverables": [
        "deliverables",
        "deliverables and timelines",
        "deliverables & timelines",
        "outputs",
        "expected outputs",
        "key deliverables",
        "project outputs",
    ],
    "timeline": [
        "timeline",
        "timelines",
        "schedule",
        "project timeline",
        "duration",
        "period of performance",
        "timeframe",
        "implementation schedule",
    ],
    "payment_terms": [
        "payment terms",
        "payment schedule",
        "budget",
        "remuneration",
        "fees and payment",
        "compensation",
        "professional fees",
        "payment and invoicing",
    ],
    "reporting_requirements": [
        "reporting requirements",
        "reporting",
        "monitoring and reporting",
        "monitoring and evaluation",
        "progress reporting",
    ],
    "confidentiality": [
        "confidentiality",
        "non-disclosure",
        "non disclosure",
        "data protection",
    ],
    "termination": [
        "termination",
        "termination clause",
        "termination of agreement",
        "suspension",
    ],
    "intellectual_property": [
        "intellectual property",
        "ownership of work",
        "ip rights",
        "ownership of deliverables",
    ],
    "dispute_resolution": [
        "dispute resolution",
        "governing law",
        "arbitration",
        "jurisdiction",
    ],
    "roles_and_responsibilities": [
        "roles and responsibilities",
        "roles & responsibilities",
        "responsibilities of parties",
        "obligations of the parties",
    ],
    "qualifications": [
        "qualifications",
        "eligibility criteria",
        "required expertise",
        "required qualifications",
        "personnel qualifications",
    ],
    "compliance_and_legal": [
        "compliance",
        "legal compliance",
        "statutory compliance",
        "applicable laws",
    ],
}

# Agreement headings that represent a *combination* of canonical concepts.
# When an Agreement section's heading matches one of these composite aliases,
# content from each listed component canonical key is concatenated (in the
# given order) into that single section, drawing from separate TOR sections.
COMPOSITE_SECTIONS: dict[str, list[str]] = {
    "deliverables_and_timelines": ["deliverables", "timeline"],
}
COMPOSITE_ALIASES: dict[str, list[str]] = {
    "deliverables_and_timelines": [
        "deliverables and timelines",
        "deliverables & timelines",
        "deliverables and timeline",
        "deliverables & timeline",
    ],
}

# Well-known TOR concepts that should be inserted as brand-new sections into
# the Agreement when the template doesn't already have a matching heading.
# Extend this set to auto-insert additional concepts in the future.
AUTO_INSERT_CANONICAL_KEYS: set[str] = {"objectives", "payment_terms"}


def normalize_heading(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s&]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _match_alias_table(normalized: str, table: dict[str, list[str]]) -> str | None:
    best_key: str | None = None
    best_len = 0
    for key, aliases in table.items():
        for alias in aliases:
            if alias == normalized or re.search(rf"\b{re.escape(alias)}\b", normalized):
                if len(alias) > best_len:
                    best_key = key
                    best_len = len(alias)
    return best_key


def _alias_canonical_key(heading: str) -> str | None:
    """Return the canonical section key for a heading, or None if unresolved."""
    normalized = normalize_heading(heading)
    if not normalized:
        return None
    return _match_alias_table(normalized, CANONICAL_SECTIONS)


def _composite_key_for_heading(heading: str) -> str | None:
    """Return the composite section key for a heading, or None if it isn't one."""
    normalized = normalize_heading(heading)
    if not normalized:
        return None
    return _match_alias_table(normalized, COMPOSITE_ALIASES)


def _body_snippet(section: DocSection) -> str:
    words = section.body_text.split()
    return " ".join(words[:_BODY_SNIPPET_WORDS])


def _embedding_text(section: DocSection) -> str:
    snippet = _body_snippet(section)
    return f"{section.heading}. {snippet}" if snippet else section.heading


def _fuzzy_ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize_heading(a), normalize_heading(b)).ratio()


_embedding_function = None
_embedding_unavailable = False


def _get_embedding_function():
    """Lazily load and cache the local embedding model for this process."""
    global _embedding_function, _embedding_unavailable
    if _embedding_unavailable:
        return None
    if _embedding_function is None:
        try:
            from chromadb.utils import embedding_functions

            candidate = embedding_functions.DefaultEmbeddingFunction()
            candidate(["warmup"])  # force model load/download now, fail fast if unavailable
            _embedding_function = candidate
        except Exception:
            logger.warning(
                "Semantic embedding model unavailable; falling back to fuzzy text matching.",
                exc_info=True,
            )
            _embedding_unavailable = True
            return None
    return _embedding_function


def _cosine_similarity(vec_a, vec_b) -> float:
    import numpy as np

    a = np.asarray(vec_a)
    b = np.asarray(vec_b)
    denom = (np.linalg.norm(a) * np.linalg.norm(b)) or 1.0
    return float(a.dot(b) / denom)


def map_sections(
    agreement_sections: list[DocSection],
    tor_sections: list[DocSection],
    *,
    threshold: float = MATCH_THRESHOLD,
) -> tuple[list[SectionMapping], list[SectionInsertion], bool]:
    """Map each Agreement Template section to its best-matching TOR section(s).

    Returns `(mappings, insertions, used_fallback)`:

    - `mappings` populates *existing* Agreement Template headings, ordered to
      match the template's section order. A mapping may draw from more than
      one TOR section when the Agreement heading is a recognized composite
      (see `COMPOSITE_SECTIONS`).
    - `insertions` are well-known TOR sections (see
      `AUTO_INSERT_CANONICAL_KEYS`) that have no corresponding Agreement
      heading at all, to be inserted as brand-new sections positioned right
      after wherever the nearest preceding TOR section landed.
    - `used_fallback` is True when semantic embeddings were unavailable and
      matching fell back to fuzzy text similarity.

    Sections with no confident match and no auto-insert eligibility are
    simply left out (left untouched by generation).
    """
    if not agreement_sections or not tor_sections:
        return [], [], False

    agreement_keys = [_alias_canonical_key(s.heading) for s in agreement_sections]
    tor_keys = [_alias_canonical_key(s.heading) for s in tor_sections]
    agreement_composite_keys = [_composite_key_for_heading(s.heading) for s in agreement_sections]

    tor_indices_by_key: dict[str, list[int]] = {}
    for idx, key in enumerate(tor_keys):
        if key:
            tor_indices_by_key.setdefault(key, []).append(idx)

    used_agreement: set[int] = set()
    used_tor: set[int] = set()
    accepted: list[tuple[int, SectionMapping]] = []
    # tor_index -> agreement_index it ended up populating, used to anchor insertions.
    tor_landing_agreement_index: dict[int, int] = {}

    # 1) Composite Agreement sections (e.g. "Deliverables and Timelines").
    for i, comp_key in enumerate(agreement_composite_keys):
        if comp_key is None or i in used_agreement:
            continue
        component_tor_indices: list[int] = []
        for component_key in COMPOSITE_SECTIONS[comp_key]:
            for idx in tor_indices_by_key.get(component_key, []):
                if idx not in used_tor:
                    component_tor_indices.append(idx)
                    used_tor.add(idx)
                    break
        if component_tor_indices:
            used_agreement.add(i)
            for idx in component_tor_indices:
                tor_landing_agreement_index[idx] = i
            accepted.append(
                (
                    i,
                    SectionMapping(
                        agreement_section=agreement_sections[i],
                        tor_sections=[tor_sections[idx] for idx in component_tor_indices],
                        confidence=_ALIAS_SCORE,
                        method="alias",
                    ),
                )
            )

    # 2) Direct canonical alias matches (exact concept match on both sides).
    for i, key in enumerate(agreement_keys):
        if key is None or i in used_agreement:
            continue
        for idx in tor_indices_by_key.get(key, []):
            if idx in used_tor:
                continue
            used_agreement.add(i)
            used_tor.add(idx)
            tor_landing_agreement_index[idx] = i
            accepted.append(
                (
                    i,
                    SectionMapping(
                        agreement_section=agreement_sections[i],
                        tor_sections=[tor_sections[idx]],
                        confidence=_ALIAS_SCORE,
                        method="alias",
                    ),
                )
            )
            break

    # 3) Embedding / fuzzy matching for everything still unmatched.
    remaining_agreement = [i for i in range(len(agreement_sections)) if i not in used_agreement]
    remaining_tor = [j for j in range(len(tor_sections)) if j not in used_tor]

    used_fallback = False
    if remaining_agreement and remaining_tor:
        embed_fn = _get_embedding_function()
        used_fallback = embed_fn is None
        agreement_vectors = tor_vectors = None

        if embed_fn is not None:
            try:
                agreement_vectors = embed_fn(
                    [_embedding_text(agreement_sections[i]) for i in remaining_agreement]
                )
                tor_vectors = embed_fn([_embedding_text(tor_sections[j]) for j in remaining_tor])
            except Exception:
                logger.warning(
                    "Embedding inference failed; falling back to fuzzy text matching.",
                    exc_info=True,
                )
                used_fallback = True
                agreement_vectors = tor_vectors = None

        candidates: list[tuple[float, int, int, str]] = []
        for a_pos, i in enumerate(remaining_agreement):
            for t_pos, j in enumerate(remaining_tor):
                if agreement_vectors is not None:
                    semantic = _cosine_similarity(agreement_vectors[a_pos], tor_vectors[t_pos])
                    fuzzy = _fuzzy_ratio(agreement_sections[i].heading, tor_sections[j].heading)
                    score = _SEMANTIC_WEIGHT * semantic + _FUZZY_WEIGHT * fuzzy
                    candidates.append((score, i, j, "semantic"))
                else:
                    score = _fuzzy_ratio(agreement_sections[i].heading, tor_sections[j].heading)
                    candidates.append((score, i, j, "fuzzy"))

        candidates.sort(key=lambda c: c[0], reverse=True)
        for score, i, j, method in candidates:
            if i in used_agreement or j in used_tor or score < threshold:
                continue
            used_agreement.add(i)
            used_tor.add(j)
            tor_landing_agreement_index[j] = i
            accepted.append(
                (
                    i,
                    SectionMapping(
                        agreement_section=agreement_sections[i],
                        tor_sections=[tor_sections[j]],
                        confidence=round(score, 3),
                        method=method,  # type: ignore[arg-type]
                    ),
                )
            )

    accepted.sort(key=lambda pair: pair[0])
    mappings = [mapping for _, mapping in accepted]

    # 4) New-section insertions for well-known, still-unused TOR concepts,
    # anchored right after wherever the nearest preceding TOR section landed
    # (chaining off a previous insertion if several are inserted back-to-back).
    insertions: list[SectionInsertion] = []
    anchor_kind = "start"
    anchor_agreement_section: DocSection | None = None
    anchor_insertion_index: int | None = None

    for idx, tor_section in enumerate(tor_sections):
        if idx in tor_landing_agreement_index:
            anchor_kind = "agreement_section"
            anchor_agreement_section = agreement_sections[tor_landing_agreement_index[idx]]
            anchor_insertion_index = None
            continue
        if idx in used_tor:
            continue
        if tor_keys[idx] in AUTO_INSERT_CANONICAL_KEYS:
            insertions.append(
                SectionInsertion(
                    heading=tor_section.heading,
                    tor_sections=[tor_section],
                    anchor_kind=anchor_kind,  # type: ignore[arg-type]
                    anchor_agreement_section=anchor_agreement_section,
                    anchor_insertion_index=anchor_insertion_index,
                )
            )
            used_tor.add(idx)
            anchor_kind = "insertion"
            anchor_insertion_index = len(insertions) - 1
            anchor_agreement_section = None

    return mappings, insertions, used_fallback
