"""TASK-056: safe FTS5 query construction.

The load-bearing property: extracted terms mirror the real
``unicode61 remove_diacritics 2`` tokenizer — proven against a live FTS5
vocab table, not assumed.
"""
from __future__ import annotations

import sqlite3

import pytest

from another_brain.domain.models import RecentFilters
from another_brain.retrieval.query import (
    build_match_query,
    extract_terms,
    live_where,
)

TOKENIZER_SAMPLES = [
    "đường phố Hà Nội",
    "tưới cây mỗi sáng",
    "Water the basil pots every 3 days",
    "ZXQ-8842",
    "run RUNID-8800/2026",
    "/var/log/another-brain/server.log",
    "café naïve résumé",
    "cøntent łódź straße Æsir ﬁle",
    "番号 1234",
    "kwojek zalquin",
    "mixed_Case-With_Separators.v2",
    "e=mc^2 & E=MC^2",
]


def _fts_terms(samples: list[str]) -> set[str]:
    """Ground truth: terms produced by the locked FTS5 tokenizer."""
    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE VIRTUAL TABLE f USING fts5(t, tokenize='unicode61 remove_diacritics 2')"
    )
    con.execute("CREATE VIRTUAL TABLE v USING fts5vocab(f, 'instance')")
    for sample in samples:
        con.execute("INSERT INTO f(t) VALUES (?)", (sample,))
    try:
        return {row[0] for row in con.execute("SELECT DISTINCT term FROM v")}
    finally:
        con.close()


def test_extract_terms_matches_locked_tokenizer():
    extracted = {term for sample in TOKENIZER_SAMPLES for term in extract_terms(sample)}
    assert extracted == _fts_terms(TOKENIZER_SAMPLES)


def test_vietnamese_no_diacritic_matches_diacritic_text():
    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE VIRTUAL TABLE f USING fts5(t, tokenize='unicode61 remove_diacritics 2')"
    )
    con.execute("INSERT INTO f(t) VALUES ('tưới cây định kỳ')")
    query = build_match_query("tuoi cay dinh ky")
    rows = con.execute("SELECT COUNT(*) FROM f WHERE f MATCH ?", (query,)).fetchone()
    assert rows[0] == 1


@pytest.mark.parametrize("text", ["?!?!", "...", "—–—", ",,,", ";;;", "???", "  \t ", "-_@#"])
def test_punctuation_only_yields_no_lexical_branch(text):
    assert extract_terms(text) == []
    assert build_match_query(text) is None


def test_ids_and_paths_tokenize_predictably():
    assert extract_terms("ZXQ-8842") == ["zxq", "8842"]
    # repeated path segments are deduped after tokenization
    assert extract_terms("/var/log/app.log") == ["var", "log", "app"]


def test_duplicate_terms_deduped_order_preserving():
    assert extract_terms("run run RUN Run") == ["run"]
    assert extract_terms("beta alpha beta") == ["beta", "alpha"]


@pytest.mark.parametrize(
    "hostile",
    [
        '" OR "x',
        'zxq" NEAR/2("',
        "topic:secret",
        "*",
        '"(payload)" AND brain_id = 1',
        "a\x00b",
        "'; DROP TABLE memories; --",
    ],
)
def test_hostile_input_is_quoted_literal_data(hostile):
    """MATCH syntax can never be injected: terms are alnum-only + quoted."""
    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE VIRTUAL TABLE f USING fts5(t, tokenize='unicode61 remove_diacritics 2')"
    )
    con.execute("INSERT INTO f(t) VALUES ('zxq secret payload drop table')")
    query = build_match_query(hostile)
    if query is None:
        return  # no safe terms → no lexical branch at all
    # Must not raise a MATCH syntax error, and every emitted term is quoted.
    con.execute("SELECT COUNT(*) FROM f WHERE f MATCH ?", (query,)).fetchone()
    for part in query.split(" OR "):
        assert part.startswith('"') and part.endswith('"')
        assert part[1:-1].isalnum()


def test_live_where_mandatory_filters_first():
    where, params = live_where(
        brain_id="b1", filters=None, now_ms=123
    )
    assert where == (
        "m.brain_id = ? AND m.deleted_at_ms IS NULL AND m.expires_at_ms > ?"
    )
    assert params == ["b1", 123]


def test_live_where_keeps_optional_filters():
    filters = RecentFilters(topic="t", catalog="c", since_ms=1, until_ms=2)
    where, params = live_where(
        brain_id="b1", filters=filters, now_ms=123
    )
    assert where == (
        "m.brain_id = ? AND m.deleted_at_ms IS NULL AND m.expires_at_ms > ?"
        " AND m.topic = ? AND m.catalog = ? AND m.created_at_ms >= ?"
        " AND m.created_at_ms <= ?"
    )
    assert params == ["b1", 123, "t", "c", 1, 2]


def test_live_where_keeps_importance_filter():
    where, params = live_where(
        brain_id="b1",
        filters=RecentFilters(topic="t", min_importance=3), now_ms=123
    )
    assert where == (
        "m.brain_id = ? AND m.deleted_at_ms IS NULL AND m.expires_at_ms > ?"
        " AND m.topic = ? AND m.importance >= ?"
    )
    assert params == ["b1", 123, "t", 3]
