"""Unit tests for memory domain models (Step 04 sections 1 and 6.5)."""
from datetime import datetime, timezone

import pytest

from errors import ValidationError
from memory.models import (
    GLOBAL_SCOPE_ID,
    EmbeddingVector,
    MemoryCatalog,
    MemoryIdentity,
    MemoryRecord,
    MemoryScope,
    SearchFilters,
    timeline_day_from_ts,
    validate_slug,
)

TZ = "Asia/Ho_Chi_Minh"


def make_record(**overrides):
    kwargs = dict(
        brain_id="flowerf-main",
        agent_id="agent-a",
        scope="user",
        scope_id="flowerf",
        topic="redis-index",
        summary="Chose PREFIX ab:memory: so audit keys are never indexed.",
        tz_name=TZ,
        now_ts=1_752_200_000.0,
    )
    kwargs.update(overrides)
    return MemoryRecord.new(**kwargs)


class TestSlugValidation:
    @pytest.mark.parametrize("value", ["bug", "bug-fix", "redis2", "a-b-c"])
    def test_valid_slugs(self, value):
        assert validate_slug(value, "topic") == value

    @pytest.mark.parametrize("value", ["", "Bug", "bug_fix", "-bug", "bug-", "bug fix", None])
    def test_invalid_slugs(self, value):
        with pytest.raises(ValidationError):
            validate_slug(value, "topic")


class TestTimelineDay:
    def test_derived_in_configured_timezone_not_utc(self):
        # 2026-07-10 18:30 UTC is already 2026-07-11 01:30 in Asia/Ho_Chi_Minh.
        ts = datetime(2026, 7, 10, 18, 30, tzinfo=timezone.utc).timestamp()
        assert timeline_day_from_ts(ts, TZ) == "2026-07-11"
        assert timeline_day_from_ts(ts, "UTC") == "2026-07-10"


class TestMemoryIdentity:
    def test_global_scope_pins_scope_id(self):
        with pytest.raises(ValidationError):
            MemoryIdentity(
                memory_id="m1", brain_id="b1", agent_id="a1",
                scope=MemoryScope.GLOBAL, scope_id="flowerf",
            )
        identity = MemoryIdentity(
            memory_id="m1", brain_id="b1", agent_id="a1",
            scope=MemoryScope.GLOBAL, scope_id=GLOBAL_SCOPE_ID,
        )
        assert identity.scope_id == "global"

    @pytest.mark.parametrize("field", ["memory_id", "brain_id"])
    def test_key_segments_reject_colon(self, field):
        kwargs = dict(
            memory_id="m1", brain_id="b1", agent_id="a1",
            scope="user", scope_id="u1",
        )
        kwargs[field] = "bad:value"
        with pytest.raises(ValidationError):
            MemoryIdentity(**kwargs)

    def test_scope_parsed_from_string(self):
        identity = MemoryIdentity(
            memory_id="m1", brain_id="b1", agent_id="a1",
            scope="project", scope_id="another-brain",
        )
        assert identity.scope is MemoryScope.PROJECT

    def test_invalid_scope_rejected(self):
        with pytest.raises(ValidationError):
            MemoryIdentity(
                memory_id="m1", brain_id="b1", agent_id="a1",
                scope="channel", scope_id="c1",
            )


class TestMemoryRecord:
    def test_defaults(self):
        record = make_record()
        assert record.catalog == MemoryCatalog.NOTE
        assert record.content == ""
        assert record.has_content is False
        assert record.importance == 3
        assert record.metadata == {}
        assert record.deleted_at is None
        assert record.is_deleted is False
        assert record.schema_version == 1
        assert record.period_end == record.period_start
        assert record.created_at == record.updated_at == 1_752_200_000.0
        assert record.identity.memory_id  # uuid generated

    def test_timeline_day_derived_from_period_start(self):
        ts = datetime(2026, 7, 10, 18, 30, tzinfo=timezone.utc).timestamp()
        record = make_record(period_start=ts)
        assert record.timeline_day == "2026-07-11"

    def test_open_catalog_accepts_new_slug(self):
        record = make_record(catalog="ci-failure")
        assert record.catalog == "ci-failure"
        assert "ci-failure" not in MemoryCatalog.STARTER

    @pytest.mark.parametrize("importance", [0, 6, True, "3"])
    def test_importance_bounds(self, importance):
        with pytest.raises(ValidationError):
            make_record(importance=importance)

    def test_blank_summary_rejected(self):
        with pytest.raises(ValidationError):
            make_record(summary="   ")

    def test_invalid_topic_rejected(self):
        with pytest.raises(ValidationError):
            make_record(topic="Redis Index")

    def test_period_end_before_start_rejected(self):
        with pytest.raises(ValidationError):
            make_record(period_start=200.0, period_end=100.0)

    def test_has_content(self):
        record = make_record(content="- [ ] reproduce\n- [x] fix")
        assert record.has_content is True


class TestEmbeddingVector:
    def test_from_list_validates_dim(self):
        vector = EmbeddingVector.from_list([0.1] * 640, expected_dim=640)
        assert vector.dim == 640
        with pytest.raises(ValidationError):
            EmbeddingVector.from_list([0.1] * 384, expected_dim=640)

    def test_rejects_non_numeric_and_non_finite(self):
        with pytest.raises(ValidationError):
            EmbeddingVector.from_list(["a", "b"], expected_dim=2)
        with pytest.raises(ValidationError):
            EmbeddingVector.from_list([0.1, float("nan")], expected_dim=2)
        with pytest.raises(ValidationError):
            EmbeddingVector.from_list([], expected_dim=0)


class TestSearchFilters:
    def test_global_scope_pins_scope_id(self):
        with pytest.raises(ValidationError):
            SearchFilters(scope="global", scope_id="flowerf")
        filters = SearchFilters(scope="global", scope_id="global")
        assert filters.scope is MemoryScope.GLOBAL

    def test_time_range_order(self):
        with pytest.raises(ValidationError):
            SearchFilters(scope="user", scope_id="u1", since_ts=200.0, until_ts=100.0)

    def test_optional_filters_validated(self):
        with pytest.raises(ValidationError):
            SearchFilters(scope="user", scope_id="u1", topic="Bad Topic")
        with pytest.raises(ValidationError):
            SearchFilters(scope="user", scope_id="u1", min_importance=9)
        with pytest.raises(ValidationError):
            SearchFilters(scope="user", scope_id="u1", timeline_day="11-07-2026")
        filters = SearchFilters(
            scope="user", scope_id="u1",
            topic="redis-index", catalog="bug",
            timeline_day="2026-07-11", min_importance=4,
        )
        assert filters.min_importance == 4
