"""TASK-032: structural validation of the retrieval bug-fix fixture.

The behavioral assertions (lexical-only survival, vector floor, live
filtering) land with the retrieval implementation in GOAL-012 (TASK-062).
Here we lock the fixture's internal consistency so those tests inherit sound
data."""
import json
import math
from pathlib import Path

import pytest

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "retrieval" / "bugfix-v1.json")
    .read_text(encoding="utf-8")
)
MEMORIES = {m["memory_id"]: m for m in FIXTURE["memories"]}
E1 = [1.0] + [0.0] * (FIXTURE["vector_dim"] - 1)


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb)


def test_vector_dim_consistent():
    for m in FIXTURE["memories"]:
        assert len(m["vector"]) == FIXTURE["vector_dim"]


def test_engineered_cosines_match_labels():
    for m in FIXTURE["memories"]:
        assert cosine(m["vector"], E1) == pytest.approx(
            m["expected_cosine_to_e1"], abs=1e-9
        )


def test_stale_rows_are_stale_at_fixture_clock():
    now = FIXTURE["now_ms"]
    assert MEMORIES["m-deleted"]["deleted_at_ms"] is not None
    assert MEMORIES["m-expired"]["expires_at_ms"] <= now
    for live in ("m-content-id", "m-semantic-low", "m-semantic-ok", "m-live-tail"):
        assert MEMORIES[live]["deleted_at_ms"] is None
        assert MEMORIES[live]["expires_at_ms"] > now


def test_case_references_exist_and_floor_labels_are_true():
    floor = FIXTURE["contract"]["cosine_floor"]
    for case in FIXTURE["cases"]:
        expect = case["expect"]
        refs = set(expect.get("include", [])) | set(expect.get("exclude", []))
        assert refs <= set(MEMORIES)
        for mid in expect.get("cosine_below_floor", []):
            assert cosine(MEMORIES[mid]["vector"], case["query_vector"]) < floor


def test_starvation_case_ordering_assumption():
    """Case 3 relies on stale rows outranking m-live-tail under the lexical
    tie-break (bm25 ASC, memory_id ASC) when they are (incorrectly) counted."""
    content_matches = sorted(
        m["memory_id"] for m in FIXTURE["memories"] if "ZXQ-8842" in m["content"]
    )
    assert content_matches == ["m-content-id", "m-deleted", "m-expired", "m-live-tail"]
    stale_first_two = content_matches[:2]
    assert "m-live-tail" not in stale_first_two or len(content_matches) <= 2
    # with the override limit of 2, counting stale rows would starve m-live-tail
    case = next(c for c in FIXTURE["cases"] if c["id"] == "live-filter-before-branch-limits")
    assert case["branch_limit_override"] == 2
    assert "m-live-tail" in content_matches[2:]


def test_query_terms_match_nothing_for_vector_only_case():
    case = next(c for c in FIXTURE["cases"] if c["id"] == "vector-only-below-floor-excluded")
    terms = case["query_text"].split()
    for m in FIXTURE["memories"]:
        haystack = f"{m['topic']} {m['summary']} {m['content']}"
        assert not any(t in haystack for t in terms)
