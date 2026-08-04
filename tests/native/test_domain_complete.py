from __future__ import annotations

import math

import pytest

from another_brain.domain.models import (
    MemoryRecord,
    MemoryScope,
    SearchFilters,
    normalize_scope,
    timeline_day,
    validate_vector,
)
from another_brain.domain.retention import TTL_SECONDS, expires_at_ms
from another_brain.errors import ValidationError


def make_record(**overrides) -> MemoryRecord:
    values = {
        "brain_id": "brain",
        "agent_id": "pytest",
        "scope": "project",
        "scope_id": "another-brain",
        "topic": "native-runtime",
        "summary": "Native runtime works.",
        "timezone": "Asia/Ho_Chi_Minh",
        "now_ms": 1_800_000_000_000,
    }
    values.update(overrides)
    return MemoryRecord.new(**values)


def test_global_scope_is_normalized_and_scoped_ids_are_required():
    assert normalize_scope("global", "") == (MemoryScope.GLOBAL, "global")
    assert normalize_scope("global", "global") == (MemoryScope.GLOBAL, "global")
    with pytest.raises(ValidationError, match="scope_id is required"):
        normalize_scope("project", "")
    with pytest.raises(ValidationError, match="must not contain ':'"):
        normalize_scope("user", "user:1")


@pytest.mark.parametrize("scope", [None, "", "workspace", 1])
def test_invalid_scope_is_rejected(scope):
    with pytest.raises(ValidationError, match="scope must be"):
        normalize_scope(scope, "id")


@pytest.mark.parametrize("field,value", [
    ("topic", "Native Runtime"),
    ("topic", "native_runtime"),
    ("catalog", ""),
    ("catalog", "Bug Fix"),
])
def test_topic_and_catalog_require_lowercase_kebab(field, value):
    with pytest.raises(ValidationError, match=field):
        make_record(**{field: value})


@pytest.mark.parametrize("importance", [0, 6, True, "3", None])
def test_importance_must_be_an_integer_from_one_to_five(importance):
    with pytest.raises(ValidationError, match="importance"):
        make_record(importance=importance)


def test_record_normalizes_text_and_strict_json_metadata():
    record = make_record(summary="  trimmed summary  ", metadata={"ok": True})
    assert record.summary == "trimmed summary"
    assert record.metadata == {"ok": True}
    with pytest.raises(ValidationError, match="strict JSON"):
        make_record(metadata={"bad": math.nan})


def test_record_rejects_invalid_period_and_identity():
    with pytest.raises(ValidationError, match="period_end"):
        make_record(period_start_ms=20, period_end_ms=10)
    with pytest.raises(ValidationError, match="brain_id"):
        make_record(brain_id="bad:id")


@pytest.mark.parametrize("importance,seconds", sorted(TTL_SECONDS.items()))
def test_retention_table_is_persisted_as_absolute_expiry(importance, seconds):
    now = 1_800_000_000_000
    assert expires_at_ms(importance, now) == now + seconds * 1_000
    assert make_record(importance=importance, now_ms=now).expires_at_ms == now + seconds * 1_000


def test_timeline_day_uses_configured_timezone():
    assert timeline_day(0, "Asia/Ho_Chi_Minh") == "1970-01-01"
    assert timeline_day(1_704_042_000_000, "Asia/Ho_Chi_Minh") == "2024-01-01"


def test_vector_validation_rejects_wrong_dimension_and_non_finite_values():
    assert validate_vector([0] * 640) == (0.0,) * 640
    with pytest.raises(ValidationError, match="640 finite"):
        validate_vector([0] * 639)
    invalid = [0.0] * 640
    invalid[1] = math.inf
    with pytest.raises(ValidationError, match="640 finite"):
        validate_vector(invalid)


@pytest.mark.xfail(strict=True, reason="SearchFilters.create does not validate filter fields yet")
@pytest.mark.parametrize("kwargs", [
    {"topic": "Bad Topic"},
    {"catalog": "Bad Catalog"},
    {"timeline_day": "not-a-date"},
    {"min_importance": 99},
])
def test_search_filters_reject_invalid_contract_values(kwargs):
    with pytest.raises(ValidationError):
        SearchFilters.create("global", "", **kwargs)
