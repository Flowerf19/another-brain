"""TASK-031: structural validation of the legacy-baseline fixture.

Locks the internal consistency of the oracle export
(``tests/fixtures/legacy-baseline/behavior-v1.json``) so the GOAL-013
preservation tests (TASK-065) inherit sound expectations. The behavioral
replay against the clean MemoryService lands with that phase.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "legacy-baseline" / "behavior-v1.json")
    .read_text(encoding="utf-8")
)
SCENARIOS = {s["id"]: s for s in FIXTURE["scenarios"]}


def test_oracle_identity_recorded():
    assert FIXTURE["oracle"]["commit"] == "edc0e573a10bb8ea9148c9830cf19fe15f757972"


def test_all_behavior_areas_covered():
    expected = {
        "identity-binding", "append-only-writes", "ttl-importance",
        "expired-excluded", "reinforce-rearms", "soft-delete-restore-grace",
        "recent-ordering", "audit-privacy", "mcp-preview-shapes",
        "health-shape", "legacy-cosine-gate-drops-content-match",
        "punctuation-query-knn-only",
    }
    assert expected <= set(SCENARIOS)


def test_ttl_table_is_the_locked_one():
    ttls = SCENARIOS["ttl-importance"]["legacy"]["ttl_days_by_importance"]
    assert ttls == {"5": 365.0, "4": 180.0, "3": 90.0, "2": 30.0, "1": 7.0}


def test_lifecycle_outcomes_are_serializable_sane():
    grace = SCENARIOS["soft-delete-restore-grace"]["legacy"]
    assert grace["get_after_forget"] == "not_found"
    assert grace["restore_status"] == "applied"
    assert grace["restore_after_hard_delete"] == "not_found"
    assert grace["grace_expiry_seconds"] == pytest.approx(3600.0, abs=5.0)


def test_audit_fixture_is_secret_free_and_complete():
    audit = SCENARIOS["audit-privacy"]["legacy"]
    assert audit["forbidden_keys_present"] == []
    assert audit["audit_survives_hard_delete"] is True
    assert {"remember", "forget", "restore", "hard_delete"} <= set(audit["actions"])


def test_previews_never_carry_content():
    previews = SCENARIOS["mcp-preview-shapes"]["legacy"]
    assert previews["previews_never_carry_content"] is True
    assert "content" not in previews["search_preview_keys"]
    assert "content" not in previews["recent_preview_keys"]
    assert "content" in previews["get_keys"]


def test_intentional_difference_is_documented():
    bug = SCENARIOS["legacy-cosine-gate-drops-content-match"]["legacy"]
    # The legacy universal cosine gate dropped the content-only match; the
    # clean branch returns it via the lexical branch (TASK-008 records this).
    assert bug["content_match_returned"] is False
