"""TASK-048 models-first: every table's domain record validates its locked
constraints; field names follow the JSONL v1 contract."""
from __future__ import annotations

import numpy as np
import pytest

from another_brain.domain.models import (
    AuditAction,
    AuditEvent,
    EmbeddingProfile,
    EmbeddingVector,
    ImportRun,
    ImportStatus,
    MemoryRecord,
    RecentFilters,
    SearchPreview,
)
from another_brain.errors import ValidationError
from another_brain.services.embedding.model_manifest import MODEL_MANIFEST
from another_brain.protocols import GLOBAL_SCOPE_ID, Scope

UNIT = np.full(640, 1.0 / np.sqrt(640), dtype=np.float32)


def _record(**overrides) -> MemoryRecord:
    base = dict(
        memory_id="mem-1",
        brain_id="default",
        agent_id="agent-a",
        scope=Scope.USER,
        scope_id="user-1",
        topic="sqlite-benchmark",
        catalog="engineering",
        summary="notes about sqlite",
        content="",
        timeline_day="2026-08-04",
        period_start_ms=1_785_000_000_000,
        period_end_ms=1_785_000_000_100,
        created_at_ms=1_785_000_000_000,
        updated_at_ms=1_785_000_000_000,
        importance=3,
        expires_at_ms=1_785_000_086_400_000,
        deleted_at_ms=None,
        metadata={},
        profile_id="q4",
        record_version=1,
        embedding=EmbeddingVector(values=UNIT),
    )
    base.update(overrides)
    return MemoryRecord(**base)


class TestMemoryRecord:
    def test_valid_record(self):
        record = _record()
        assert record.scope is Scope.USER
        assert record.embedding.values.shape == (640,)

    def test_scope_string_coerced_and_global_pinned(self):
        record = _record(scope="global", scope_id=GLOBAL_SCOPE_ID)
        assert record.scope is Scope.GLOBAL
        with pytest.raises(ValidationError, match="canonicalizes"):
            _record(scope=Scope.GLOBAL, scope_id="other")

    @pytest.mark.parametrize("field", ["memory_id", "brain_id", "agent_id", "topic", "catalog", "summary", "profile_id"])
    def test_non_empty_identity_and_text(self, field):
        with pytest.raises(ValidationError, match="non-empty"):
            _record(**{field: "  "})

    def test_content_may_be_empty(self):
        assert _record(content="").content == ""

    @pytest.mark.parametrize("importance", [0, 6, -1])
    def test_importance_range(self, importance):
        with pytest.raises(ValidationError, match="1..5"):
            _record(importance=importance)

    def test_ordered_period(self):
        with pytest.raises(ValidationError, match="ordered"):
            _record(period_start_ms=200, period_end_ms=100)

    def test_updated_after_created(self):
        with pytest.raises(ValidationError, match="updated_at"):
            _record(created_at_ms=200, updated_at_ms=100)

    def test_metadata_must_be_object(self):
        with pytest.raises(ValidationError, match="JSON object"):
            _record(metadata=["not", "a", "dict"])

    def test_record_version_positive(self):
        with pytest.raises(ValidationError, match="positive"):
            _record(record_version=0)

    def test_bad_timeline_day(self):
        with pytest.raises(ValidationError, match="YYYY-MM-DD"):
            _record(timeline_day="04-08-2026")

    def test_embedding_type_checked(self):
        with pytest.raises(ValidationError, match="EmbeddingVector"):
            _record(embedding=np.zeros(640))  # type: ignore[arg-type]


class TestAuditEvent:
    def _event(self, **overrides) -> AuditEvent:
        base = dict(
            event_id="evt-1",
            brain_id="default",
            memory_id="mem-1",
            agent_id="agent-a",
            action=AuditAction.FORGET,
            event_at_ms=1_785_000_000_000,
            timeline_day="2026-08-04",
            detail={"previous_expires_at_ms": 123, "source": "tool"},
        )
        base.update(overrides)
        return AuditEvent(**base)

    def test_valid_event_and_action_coercion(self):
        event = self._event(action="restore")
        assert event.action is AuditAction.RESTORE

    @pytest.mark.parametrize(
        "detail",
        [
            {"topic": "leak"},
            {"nested": {"summary": "leak"}},
            {"list": [{"content": "leak"}]},
            {"deep": {"deeper": [{"metadata": {"x": 1}}]}},
        ],
    )
    def test_memory_text_forbidden_anywhere(self, detail):
        with pytest.raises(ValidationError, match="memory text"):
            self._event(detail=detail)

    def test_structural_detail_allowed(self):
        event = self._event(detail={"expires_at_ms": 123, "importance": 3})
        assert event.detail["expires_at_ms"] == 123

    def test_action_must_be_known(self):
        with pytest.raises(ValueError):
            self._event(action="update")

    def test_bad_timeline_day(self):
        with pytest.raises(ValidationError, match="YYYY-MM-DD"):
            self._event(timeline_day="nope")


class TestImportRun:
    def _run(self, **overrides) -> ImportRun:
        base = dict(
            export_id="00000000-0000-4000-8000-000000000001",
            artifact_sha256="a" * 64,
            format_version=1,
            status=ImportStatus.RUNNING,
            last_committed_seq=42,
            imported_count=42,
            skipped_count=1,
            failed_count=0,
            started_at_ms=1_785_000_000_000,
            completed_at_ms=None,
        )
        base.update(overrides)
        return ImportRun(**base)

    def test_valid_and_status_coercion(self):
        run = self._run(status="completed", completed_at_ms=1_785_000_001_000)
        assert run.status is ImportStatus.COMPLETED

    def test_bad_sha(self):
        with pytest.raises(ValidationError, match="64 hex"):
            self._run(artifact_sha256="zz")

    def test_negative_counters(self):
        with pytest.raises(ValidationError, match="non-negative"):
            self._run(imported_count=-1)

    def test_running_with_completion_rejected(self):
        with pytest.raises(ValidationError, match="completed_at_ms"):
            self._run(status=ImportStatus.RUNNING, completed_at_ms=123)


class TestEmbeddingProfile:
    def test_from_manifest_matches_locked_values(self):
        profile = EmbeddingProfile.from_manifest(MODEL_MANIFEST, created_at_ms=1)
        assert profile.profile_id == MODEL_MANIFEST.profile
        assert profile.model_repo == MODEL_MANIFEST.repo
        assert profile.model_revision == MODEL_MANIFEST.revision
        assert profile.variant == "q4"
        assert profile.dimension == 640
        assert profile.dtype == "float32"
        assert profile.normalized is True
        files = dict(MODEL_MANIFEST.files)
        assert profile.tokenizer_sha256 == files["tokenizer.json"]
        assert profile.config_sha256 == files["config.json"]
        assert profile.prompt_utf8_sha256 == MODEL_MANIFEST.query_prompt_utf8_sha256
        assert profile.query_prompt == MODEL_MANIFEST.query_prompt
        assert profile.input_version == 2
        assert profile.created_at_ms == 1

    def test_invalid_hashes_rejected(self):
        with pytest.raises(ValidationError, match="64 hex"):
            EmbeddingProfile(
                profile_id="q4", model_repo="r", model_revision="v", variant="q4",
                dimension=640, dtype="float32", normalized=True,
                tokenizer_sha256="short", config_sha256="a" * 64,
                prompt_utf8_sha256="a" * 64, query_prompt="q", input_version=2,
                created_at_ms=1,
            )


class TestRecentFilters:
    def test_empty_filters(self):
        assert RecentFilters().topic is None

    def test_time_window_ordered(self):
        with pytest.raises(ValidationError, match="since_ms"):
            RecentFilters(since_ms=200, until_ms=100)

    def test_non_empty_strings(self):
        with pytest.raises(ValidationError, match="topic filter"):
            RecentFilters(topic="  ")


class TestSearchPreview:
    def test_valid_preview(self):
        preview = SearchPreview(
            memory_id="mem-1", topic="t", catalog="c", summary="s", scope="user",
            scope_id="u1", timeline_day="2026-08-04", created_at_ms=1,
            importance=3, expires_at_ms=2, has_content=True,
        )
        assert preview.scope is Scope.USER
        assert preview.has_content is True

    def test_no_content_or_embedding_field(self):
        # contract: previews never carry content or the embedding
        assert not hasattr(SearchPreview, "content")
        assert not hasattr(SearchPreview, "embedding")
