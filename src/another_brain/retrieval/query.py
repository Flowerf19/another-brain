"""Safe FTS5 query construction from Unicode text (TASK-056).

User text never enters the MATCH string: terms are extracted exactly the way
the locked ``unicode61 remove_diacritics 2`` tokenizer indexes them —
canonical (NFD) decomposition, combining marks stripped, lowercased, split
on every non-alphanumeric character — then each term is double-quoted and
OR-combined. Quoted alnum-only terms cannot express MATCH operators
(AND/OR/NOT/NEAR/column filters), so injection is structurally impossible.

Behavior locked by the retrieval contract:

- punctuation-only input yields no terms → :func:`build_match_query` returns
  ``None`` and the hybrid retriever runs the vector branch alone;
- names/IDs/paths tokenize predictably: ``ZXQ-8842`` → ``zxq, 8842``,
  ``/var/log/app.log`` → ``var, log, app, log``;
- Vietnamese no-diacritic queries match diacritic documents
  (``tuoi cay`` matches ``tưới cây``) because both sides strip marks;
- duplicate terms are deduped (order-preserving) so adversarial repetition
  cannot inflate the MATCH string.

Also home to :func:`scoped_live_where`, the single builder of the mandatory
``brain/scope/live`` (+ optional ``RecentFilters``) WHERE fragment shared by
both retrieval branches — identical filtered IDs are the parity contract
between the sqlite-vec and NumPy vector adapters.
"""
from __future__ import annotations

import unicodedata

from another_brain.domain.models import RecentFilters
from another_brain.protocols import ScopeKey


def extract_terms(text: str) -> list[str]:
    """Tokenizer-compatible terms, deduplicated, in first-seen order."""
    folded = "".join(
        ch for ch in unicodedata.normalize("NFD", text) if not unicodedata.combining(ch)
    ).lower()
    terms: list[str] = []
    seen: set[str] = set()
    current: list[str] = []
    for ch in folded:
        if ch.isalnum():
            current.append(ch)
        elif current:
            term = "".join(current)
            current = []
            if term not in seen:
                seen.add(term)
                terms.append(term)
    if current:
        term = "".join(current)
        if term not in seen:
            terms.append(term)
    return terms


def build_match_query(text: str) -> str | None:
    """Safe OR MATCH query over extracted terms; ``None`` when term-free."""
    terms = extract_terms(text)
    if not terms:
        return None
    return " OR ".join(f'"{term}"' for term in terms)


def scoped_live_where(
    *,
    brain_id: str,
    scope: ScopeKey,
    filters: RecentFilters | None,
    now_ms: int,
) -> tuple[str, list[object]]:
    """Mandatory brain/scope/live WHERE fragment plus optional narrowing.

    Live means ``deleted_at_ms IS NULL AND expires_at_ms > now``; the filter
    applies before every branch limit so stale rows never starve candidates.
    """
    where = [
        "m.brain_id = ?",
        "m.scope = ?",
        "m.scope_id = ?",
        "m.deleted_at_ms IS NULL",
        "m.expires_at_ms > ?",
    ]
    params: list[object] = [brain_id, scope.scope.value, scope.scope_id, now_ms]
    if filters is not None:
        if filters.topic is not None:
            where.append("m.topic = ?")
            params.append(filters.topic)
        if filters.catalog is not None:
            where.append("m.catalog = ?")
            params.append(filters.catalog)
        if filters.since_ms is not None:
            where.append("m.created_at_ms >= ?")
            params.append(filters.since_ms)
        if filters.until_ms is not None:
            where.append("m.created_at_ms <= ?")
            params.append(filters.until_ms)
        if filters.timeline_day is not None:
            where.append("m.timeline_day = ?")
            params.append(filters.timeline_day)
        if filters.min_importance is not None:
            where.append("m.importance >= ?")
            params.append(filters.min_importance)
    return " AND ".join(where), params
