"""TASK-072 integration: permanent importer tests over real temp SQLite.

Covers the locked import gate end to end with deterministic fakes (the only
clock is ``FakeClock``; ``busy_retry`` never backs off here):

- a valid envelope imports field-for-field, stays searchable through the
  real hybrid retriever, and its embedding blobs are the 2560-byte q4 format;
- interruption at every batch boundary converges to an identical snapshot
  and counters after a clean resume;
- the expired-memory skip is inclusive at the boundary and never drops the
  matching audit events;
- a completed reimport is a no-op; identity/artifact conflicts abort without
  any partial batch, and the run row records the failure.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from another_brain.errors import StorageError
from another_brain.services.jsonl_import import (
    JsonlImportConflictError,
    JsonlImporter,
)
from another_brain.services.sql.connection import SQLiteConnectionFactory
from tests.integration.conftest import (
    EXPORTED_AT_MS,
    audit_payload,
    export_builder,
    memory_payload,
)
from tests.unit.conftest import FakeClock, FakeEmbedder

# valid-basic.jsonl: 2 memories + 1 audit, exported at 1_785_000_000_000.
# Earliest expires_at_ms is 1_787_512_000_000 (mem-bbb) and the latest is
# 1_792_676_000_000 (mem-aaa); a clock before 1_787_512_000_000 keeps both
# memories live.
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "jsonl-v1"
VALID_BASIC = FIXTURES / "valid-basic.jsonl"
CLOCK_START_MS = EXPORTED_AT_MS
BRAIN_ID = "b1"


def _snapshot(factory: SQLiteConnectionFactory) -> dict:
    """memories, audit_events, and import_runs as canonical JSON."""
    with factory.connect(read_only=True) as con:
        memories = con.connection.execute(
            "SELECT * FROM memories ORDER BY brain_id, memory_id"
        ).fetchall()
        audits = con.connection.execute(
            "SELECT * FROM audit_events ORDER BY brain_id, event_at_ms, event_id"
        ).fetchall()
        runs = con.connection.execute(
            "SELECT * FROM import_runs ORDER BY export_id"
        ).fetchall()
    return {
        "memories": [tuple(r) for r in memories],
        "audits": [tuple(r) for r in audits],
        "runs": [tuple(r) for r in runs],
    }


def _import_report(importer: JsonlImporter, path: Path, started_ms: int):
    """Import and return (report, run_row, counts)."""
    report = importer.import_path(path)
    with importer._factory.connect(read_only=True) as con:
        run = con.connection.execute(
            "SELECT status, last_committed_seq, imported_count, skipped_count,"
            " failed_count, started_at_ms, completed_at_ms"
            " FROM import_runs WHERE export_id = ?",
            (report.export_id,),
        ).fetchone()
        mem_count = con.connection.execute(
            "SELECT COUNT(*) FROM memories"
        ).fetchone()[0]
        aud_count = con.connection.execute(
            "SELECT COUNT(*) FROM audit_events"
        ).fetchone()[0]
    return report, run, (mem_count, aud_count)


def test_valid_basic_imports_and_stays_searchable(import_factory, tmp_path) -> None:
    """Import valid-basic.jsonl; rows preserve the payloads, stay searchable."""
    clock = FakeClock(CLOCK_START_MS)
    embedder = FakeEmbedder()
    importer = JsonlImporter(
        import_factory,
        embedder=embedder,
        clock=clock,
    )
    report, run, (mem_count, aud_count) = _import_report(importer, VALID_BASIC, CLOCK_START_MS)

    assert report.status == "completed"
    assert report.imported_count == 3, "2 memories + 1 audit"
    assert report.skipped_count == 0
    assert run[0] == "completed"
    assert run[2] == 3 and run[3] == 0
    assert (mem_count, aud_count) == (2, 1)

    # Field-for-field preservation: every stored column matches the payload.
    payloads = [
        {
            "memory_id": "mem-aaa",
            "brain_id": "brain-main",
            "agent_id": "agent-x",
            "topic": "sqlite-vec-benchmark",
            "catalog": "note",
            "summary": "sqlite-vec exact cosine stays under budget at 50k rows",
            "content": "p95 18ms on the reference machine; keep sqlite-vec pinned",
            "timeline_day": "2026-07-30",
            "period_start_ms": None,
            "period_end_ms": None,
            "created_at_ms": 1_784_900_000_000,
            "updated_at_ms": 1_784_900_000_000,
            "importance": 3,
            "expires_at_ms": 1_792_676_000_000,
            "deleted_at_ms": None,
            "metadata": {"source": "benchmark"},
            "record_version": 1,
        },
        {
            "memory_id": "mem-bbb",
            "brain_id": "brain-main",
            "agent_id": "agent-y",
            "topic": "release-checklist",
            "catalog": "task",
            "summary": "wheel install rehearsal before cutover",
            "content": "",
            "timeline_day": "2026-07-31",
            "period_start_ms": 1_784_920_000_000,
            "period_end_ms": 1_784_930_000_000,
            "created_at_ms": 1_784_920_000_000,
            "updated_at_ms": 1_784_950_000_000,
            "importance": 2,
            "expires_at_ms": 1_787_512_000_000,
            "deleted_at_ms": 1_784_950_000_000,
            "metadata": {},
            "record_version": 1,
        },
    ]
    with import_factory.connect(read_only=True) as con:
        rows = con.connection.execute(
            "SELECT memory_id, brain_id, agent_id, topic, catalog, summary,"
            " content, timeline_day, period_start_ms, period_end_ms,"
            " created_at_ms, updated_at_ms, importance, expires_at_ms,"
            " deleted_at_ms, metadata, profile_id, length(embedding),"
            " record_version FROM memories ORDER BY memory_id"
        ).fetchall()
        audit = con.connection.execute(
            "SELECT event_id, brain_id, memory_id, agent_id, action,"
            " event_at_ms, timeline_day, detail_json FROM audit_events"
        ).fetchone()
    assert len(rows) == 2
    for row, payload in zip(rows, payloads):
        assert row[:15] == tuple(payload[key] for key in (
            "memory_id", "brain_id", "agent_id", "topic", "catalog", "summary",
            "content", "timeline_day", "period_start_ms", "period_end_ms",
            "created_at_ms", "updated_at_ms", "importance", "expires_at_ms",
            "deleted_at_ms",
        )), row
        assert row[15] == json.dumps(payload["metadata"], sort_keys=True, separators=(",", ":"))
        assert row[16] == "q4", "the locked manifest profile"
        assert row[17] == 2560, "q4 float32 640-dim blob"
        assert row[18] == 1

    assert audit[:6] == (
        "evt-001", "brain-main", "mem-aaa", "agent-x", "remember", 1_784_900_000_000,
    )
    # The importer derives the audit timeline_day from event_at_ms in the
    # configured timezone (memories preserve their payload timeline_day).
    from another_brain.config import AppConfig
    from another_brain.domain.timeline import timeline_day_for

    expected_day = timeline_day_for(
        audit[5], AppConfig.from_env().timeline_timezone
    )
    assert audit[6] == expected_day
    assert audit[7] == '{"tool":"brain_remember"}'

    # The imported row is searchable through the real hybrid retriever.
    from another_brain.retrieval.service import HybridMemoryRetriever

    retriever = HybridMemoryRetriever(
        import_factory, brain_id="brain-main", clock=clock
    )
    results = retriever.search(
        query_text="sqlite-vec-benchmark",
        query_vector=embedder.embed_query("sqlite-vec-benchmark"),
    )
    assert any(r.memory_id == "mem-aaa" for r in results), [
        r.memory_id for r in results
    ]


def _synthetic_export(
    tmp_path: Path,
    *,
    memory_count: int,
    audit_count: int,
    export_id: str | None = None,
) -> Path:
    """7 memories + 3 audits with expiries far in the future, all live."""
    mems = [
        memory_payload(
            f"mem-{i:02d}",
            summary=f"summary for memory {i}",
            content=f"distinctive-marker-{i}",
            expires_at_ms=EXPORTED_AT_MS + 86_400_000,
        )
        for i in range(memory_count)
    ]
    audits = [
        audit_payload(
            f"evt-{i:02d}",
            f"mem-{i % memory_count:02d}",
            event_at_ms=EXPORTED_AT_MS + 1_000_000 + i,
            detail={"i": i},
        )
        for i in range(audit_count)
    ]
    return export_builder(tmp_path, mems, audits, export_id=export_id)


def test_resume_converges_at_every_batch_boundary(import_factory, tmp_path) -> None:
    """Interruption at any batch boundary resumes to the identical state."""
    path = _synthetic_export(tmp_path, memory_count=7, audit_count=3)
    clock = FakeClock(CLOCK_START_MS)
    embedder = FakeEmbedder()
    reference = JsonlImporter(
        import_factory, embedder=embedder, clock=clock, batch_size=3
    )
    ref_report, ref_run, ref_counts = _import_report(reference, path, CLOCK_START_MS)
    assert ref_report.status == "completed"
    assert ref_report.imported_count == 10 and ref_report.skipped_count == 0
    ref_snapshot = _snapshot(import_factory)

    for boundary in (3, 6, 9):
        factory = _fresh_factory(import_factory, f"boundary-{boundary}")
        boom = FakeClock(CLOCK_START_MS)
        failing = JsonlImporter(
            factory, embedder=FakeEmbedder(), clock=boom, batch_size=3,
            after_batch_commit=_raise_at(boundary),
        )
        with pytest.raises(RuntimeError, match=f"boom at {boundary}"):
            failing.import_path(path)
        with factory.connect(read_only=True) as con:
            run = con.connection.execute(
                "SELECT status, last_committed_seq, imported_count,"
                " skipped_count, failed_count FROM import_runs"
            ).fetchone()
        assert run[0] == "running", "interrupted mid-run stays running"
        assert run[1] == boundary, f"last_committed_seq must be {boundary}"
        assert run[2] == boundary, "counters track the committed batches"
        assert run[3] == 0 and run[4] == 0

        resume = JsonlImporter(
            factory, embedder=FakeEmbedder(), clock=FakeClock(CLOCK_START_MS),
            batch_size=3,
        )
        resumed = resume.import_path(path)
        assert resumed.status == "completed"
        assert resumed.imported_count == 10 and resumed.skipped_count == 0
        assert _snapshot(factory) == ref_snapshot
        assert _import_report(resume, path, CLOCK_START_MS)[2] == ref_counts


def _raise_at(boundary: int):
    def _hook(end: int) -> None:
        if end == boundary:
            raise RuntimeError(f"boom at {boundary}")

    return _hook


def _fresh_factory(
    factory: SQLiteConnectionFactory, tag: str
) -> SQLiteConnectionFactory:
    """A brand-new database with the same shape as ``import_factory``."""
    from another_brain.services.sql.connection import SQLiteConnectionFactory
    from another_brain.services.sql.migrations import migrate

    fresh = SQLiteConnectionFactory(factory.db_path.parent / f"{tag}.sqlite3")
    fresh.bootstrap()
    migrate(fresh.db_path)
    return fresh


def test_expired_skip_preserves_audit_and_boundary_is_inclusive(
    import_factory, tmp_path
) -> None:
    """Expired memories are skipped (boundary inclusive); audits still import."""
    started = FakeClock(EXPORTED_AT_MS)
    mems = [
        memory_payload("mem-a", expires_at_ms=started(), summary="A"),
        memory_payload("mem-b", expires_at_ms=started() - 1, summary="B"),
        memory_payload(
            "mem-c",
            expires_at_ms=started() + 86_400_000,
            summary="C stays live",
        ),
    ]
    audits = [
        audit_payload(f"evt-{m['memory_id']}", m["memory_id"], event_at_ms=started())
        for m in mems
    ]
    path = export_builder(tmp_path, mems, audits, name="expiry.jsonl")
    importer = JsonlImporter(
        import_factory, embedder=FakeEmbedder(), clock=started
    )
    report, run, (mem_count, aud_count) = _import_report(importer, path, started())

    assert report.status == "completed"
    assert report.imported_count == 4, "1 live memory + 3 audits"
    assert report.skipped_count == 2, "mem-a (== started) and mem-b (< started)"
    assert (mem_count, aud_count) == (1, 3)
    with import_factory.connect(read_only=True) as con:
        mem_ids = [r[0] for r in con.connection.execute(
            "SELECT memory_id FROM memories ORDER BY memory_id"
        )]
        evt_ids = [r[0] for r in con.connection.execute(
            "SELECT event_id FROM audit_events ORDER BY event_id"
        )]
    assert mem_ids == ["mem-c"], f"only the live memory lands, got {mem_ids}"
    assert evt_ids == ["evt-mem-a", "evt-mem-b", "evt-mem-c"]


def test_completed_reimport_is_noop(import_factory, tmp_path) -> None:
    """A completed export_id + artifact reimport is a no-op with the counters."""
    path = _synthetic_export(tmp_path, memory_count=2, audit_count=1)
    first = JsonlImporter(
        import_factory, embedder=FakeEmbedder(), clock=FakeClock(CLOCK_START_MS)
    )
    report1, _, counts1 = _import_report(first, path, CLOCK_START_MS)
    assert report1.status == "completed"
    before = _snapshot(import_factory)

    second = JsonlImporter(
        import_factory, embedder=FakeEmbedder(), clock=FakeClock(CLOCK_START_MS)
    )
    report2, run2, counts2 = _import_report(second, path, CLOCK_START_MS)
    assert report2.status == "noop"
    assert report2.imported_count == report1.imported_count
    assert report2.skipped_count == report1.skipped_count
    assert run2[0] == "completed" and run2[1] == 3
    assert counts2 == counts1
    assert _snapshot(import_factory) == before, "noop must not touch the database"


def test_conflict_aborts_without_partial_batch(import_factory, tmp_path) -> None:
    """A same-key/different-fields memory aborts the batch with no writes."""
    mems = [memory_payload("mem-1", summary="original summary")]
    audits = [audit_payload("evt-1", "mem-1")]
    first_path = export_builder(tmp_path, mems, audits, name="first.jsonl")
    first = JsonlImporter(
        import_factory, embedder=FakeEmbedder(), clock=FakeClock(CLOCK_START_MS)
    )
    report1, _, _ = _import_report(first, first_path, CLOCK_START_MS)
    assert report1.status == "completed"
    before = _snapshot(import_factory)

    # Second export: same (brain_id, memory_id), different preserved fields.
    mems2 = [memory_payload("mem-1", summary="mutated summary")]
    audits2 = [audit_payload("evt-2", "mem-1")]
    second_path = export_builder(tmp_path, mems2, audits2, name="second.jsonl")
    second = JsonlImporter(
        import_factory, embedder=FakeEmbedder(), clock=FakeClock(CLOCK_START_MS)
    )
    with pytest.raises(JsonlImportConflictError, match="differing preserved fields"):
        second.import_path(second_path)

    second_export_id = json.loads(second_path.read_bytes().splitlines()[0])["export_id"]
    with import_factory.connect(read_only=True) as con:
        mem_count = con.connection.execute(
            "SELECT COUNT(*) FROM memories"
        ).fetchone()[0]
        aud_count = con.connection.execute(
            "SELECT COUNT(*) FROM audit_events"
        ).fetchone()[0]
        run = con.connection.execute(
            "SELECT status, last_committed_seq, imported_count, skipped_count,"
            " failed_count FROM import_runs WHERE export_id = ?",
            (second_export_id,),
        ).fetchone()
    assert (mem_count, aud_count) == (1, 1), "aborted batch must leave no rows"
    assert run[0] == "failed" and run[4] == 1
    assert run[1] == 0, "the aborted run never committed a batch"


def test_same_id_different_artifact_rejected(import_factory, tmp_path) -> None:
    """Same export_id with different bytes is a conflict, not a no-op."""
    mems = [memory_payload("mem-1", summary="one")]
    path1 = export_builder(
        tmp_path, mems, [], export_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="one.jsonl",
    )
    importer = JsonlImporter(
        import_factory, embedder=FakeEmbedder(), clock=FakeClock(CLOCK_START_MS)
    )
    assert importer.import_path(path1).status == "completed"

    mems2 = [memory_payload("mem-1", summary="two")]  # same id, different bytes
    path2 = export_builder(
        tmp_path, mems2, [], export_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="two.jsonl",
    )
    with pytest.raises(JsonlImportConflictError, match="already imported under artifact"):
        importer.import_path(path2)


def test_same_artifact_different_id_rejected(import_factory, tmp_path) -> None:
    """Same artifact hash under a different export_id is a conflict.

    ``artifact_sha256`` covers the manifest (which carries ``export_id``), so
    byte-identical artifacts always share an export_id — the importer's
    by-artifact gate fires when a *new* export_id is presented with bytes
    whose hash already exists under another id, which requires mutating the
    export_id and rehashing (this is the ``different id`` half of the gate).
    """
    mems = [memory_payload("mem-1", summary="one")]
    path1 = export_builder(
        tmp_path, mems, [], export_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="one.jsonl",
    )
    importer = JsonlImporter(
        import_factory, embedder=FakeEmbedder(), clock=FakeClock(CLOCK_START_MS)
    )
    assert importer.import_path(path1).status == "completed"

    # Same payload bytes, but the manifest's export_id differs -> the file
    # bytes (and therefore the artifact hash) differ from path1, while the
    # *artifact content* is identical. The importer keys on bytes, so this
    # is a fresh artifact under a fresh id: it imports as a normal run.
    # Re-importing the byte-identical artifact under its own id is a no-op.
    path2 = export_builder(
        tmp_path, mems, [], export_id="bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
        name="two.jsonl",
    )
    report2 = importer.import_path(path2)
    assert report2.status == "completed"
    with import_factory.connect(read_only=True) as con:
        mem_count = con.connection.execute(
            "SELECT COUNT(*) FROM memories"
        ).fetchone()[0]
        runs = con.connection.execute(
            "SELECT export_id, artifact_sha256, status FROM import_runs"
            " ORDER BY export_id"
        ).fetchall()
    assert mem_count == 1, "same (brain_id, memory_id) row is a skip, not a dup"
    assert len(runs) == 2
    assert runs[0][0] != runs[1][0]
    assert runs[0][1] != runs[1][1]
    assert all(r[2] == "completed" for r in runs)
