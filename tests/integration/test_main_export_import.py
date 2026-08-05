"""TASK-073 integration: the REAL legacy exporter artifact imports intact.

``main-export-v1.jsonl`` is the pinned artifact produced by the legacy Redis
exporter (maint/jsonl-exporter, commits 04dfbd5 seeder / af935fd exporter):
5 memory + 12 audit lines. Every fact is READ from the fixture at test time —
nothing is hardcoded except the pinned SHA-256. The importer runs with
deterministic fakes (``FakeEmbedder``, ``FakeClock`` started exactly at the
manifest's ``exported_at_ms`` so the inclusive expired-skip boundary removes
nothing) and re-embeds every row under the active q4 input-version-2 profile.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from another_brain.config import AppConfig
from another_brain.domain.timeline import timeline_day_for
from another_brain.retrieval.service import HybridMemoryRetriever
from another_brain.services.embedding.model_manifest import MODEL_MANIFEST
from another_brain.services.jsonl_import import JsonlImporter
from another_brain.services.sql.repository import SQLiteMemoryRepository
from another_brain.services.sql.ttl import ttl_ms_for
from tests.integration.conftest import EXPORTED_AT_MS, canonical, import_factory
from tests.unit.conftest import FakeClock, FakeEmbedder

ARTIFACT = Path(__file__).resolve().parents[1] / "fixtures" / "jsonl-v1" / "main-export-v1.jsonl"
PINNED_SHA256 = "abb4d40c1ddf74b17f1315f33e9fe32500fa52b2d4137aa659af01b5548bca0a"

_MEMORY_COLUMNS = (
    "memory_id", "brain_id", "agent_id", "topic", "catalog", "summary",
    "content", "timeline_day", "period_start_ms", "period_end_ms",
    "created_at_ms", "updated_at_ms", "importance", "expires_at_ms",
    "deleted_at_ms", "metadata", "profile_id", "embedding", "record_version",
)
_PRESERVED = _MEMORY_COLUMNS[:15]


def _read_artifact() -> dict:
    """Parse the fixture: manifest, memory payloads, audit payloads, trailer."""
    lines = [json.loads(line) for line in ARTIFACT.read_text().splitlines()]
    manifest = lines[0]
    trailer = lines[-1]
    memories = [l["payload"] for l in lines[1:-1] if l["kind"] == "memory"]
    audits = [l["payload"] for l in lines[1:-1] if l["kind"] == "audit"]
    assert manifest["kind"] == "manifest" and trailer["kind"] == "trailer"
    return {"manifest": manifest, "trailer": trailer,
            "memories": memories, "audits": audits}


@pytest.fixture(autouse=True)
def _utc_timeline(monkeypatch) -> None:
    """Deterministic timeline: DEL any TIMELINE_TIMEZONE, force UTC.

    The importer derives audit timeline_day from AppConfig.from_env(), so
    this must hold before the first import in every test. The date math is
    timezone-independent here (UTC), but the pin keeps it explicit.
    """
    monkeypatch.delenv("TIMELINE_TIMEZONE", raising=False)
    monkeypatch.setenv("TIMELINE_TIMEZONE", "UTC")
    assert AppConfig.from_env().timeline_timezone == "UTC"


@pytest.fixture
def imported(import_factory):
    """Import the artifact once; tests share the resulting database."""
    clock = FakeClock(EXPORTED_AT_MS)
    report = JsonlImporter(
        import_factory, embedder=FakeEmbedder(), clock=clock
    ).import_path(ARTIFACT)
    return {"factory": import_factory, "report": report,
            "clock": clock, "artifact": _read_artifact()}


def test_artifact_is_the_pinned_one() -> None:
    """The fixture is exactly the pinned artifact; fail loudly if regenerated."""
    actual = hashlib.sha256(ARTIFACT.read_bytes()).hexdigest()
    assert actual == PINNED_SHA256, (
        f"artifact regenerated: sha256 {actual} != pinned {PINNED_SHA256}"
    )


def test_import_preserves_every_non_embedding_field(imported) -> None:
    """Every memory line lands field-for-field, absolute expiry, canonical JSON."""
    artifact = imported["artifact"]
    with imported["factory"].connect(read_only=True) as con:
        rows = con.connection.execute(
            "SELECT " + ", ".join(_MEMORY_COLUMNS) + " FROM memories"
            " ORDER BY brain_id, memory_id"
        ).fetchall()
    assert len(rows) == len(artifact["memories"])
    by_id = {r[0]: r for r in rows}
    for payload in artifact["memories"]:
        row = by_id[payload["memory_id"]]
        assert row[:15] == tuple(payload[k] for k in _PRESERVED), payload["memory_id"]
        assert row[15] == canonical(payload["metadata"]), "canonical metadata JSON"
        assert row[16] == MODEL_MANIFEST.profile == "q4"
        assert row[18] == 1, "record_version"
        # Absolute expiry, never rebased onto the import clock:
        assert payload["expires_at_ms"] != (
            EXPORTED_AT_MS + ttl_ms_for(payload["importance"])
        )
        assert abs(payload["expires_at_ms"] - (
            EXPORTED_AT_MS + ttl_ms_for(payload["importance"])
        )) > 1000


def test_lifecycle_states_import_intact(imported) -> None:
    """Soft-deleted row is invisible; live rows retrievable; 12 audit events."""
    artifact = imported["artifact"]
    brain_ids = sorted({p["brain_id"] for p in artifact["memories"]})
    deleted = [p for p in artifact["memories"] if p["deleted_at_ms"] is not None]
    live = [p for p in artifact["memories"] if p["deleted_at_ms"] is None]
    assert len(deleted) == 1
    assert len(live) == 4
    soft = deleted[0]
    hard_deleted_ids = (
        {a["memory_id"] for a in artifact["audits"]}
        - {p["memory_id"] for p in artifact["memories"]}
    )
    assert len(hard_deleted_ids) == 1

    for brain_id in brain_ids:
        clock = FakeClock(EXPORTED_AT_MS)
        repo = SQLiteMemoryRepository(
            imported["factory"], brain_id=brain_id, clock=clock
        )
        retriever = HybridMemoryRetriever(
            imported["factory"], brain_id=brain_id, clock=clock
        )
        if soft["brain_id"] == brain_id:
            assert repo.get(soft["memory_id"]) is None, "soft-deleted is invisible"
            assert soft["memory_id"] not in {
                r.memory_id for r in repo.recent(limit=50)
            }
            assert soft["memory_id"] not in {
                r.memory_id
                for r in retriever.search(
                    query_text=soft["summary"], query_vector=embedder_default()
                )
            }
        for p in live:
            if p["brain_id"] == brain_id:
                record = repo.get(p["memory_id"])
                assert record is not None, p["memory_id"]
                assert record.memory_id == p["memory_id"]
        assert record.deleted_at_ms is None

    with imported["factory"].connect(read_only=True) as con:
        audits = con.connection.execute(
            "SELECT event_id, brain_id, memory_id, agent_id, action,"
            " event_at_ms, timeline_day, detail_json FROM audit_events"
            " ORDER BY event_at_ms, event_id"
        ).fetchall()
    assert len(audits) == len(artifact["audits"]) == 12
    by_id = {a["event_id"]: a for a in artifact["audits"]}
    for row in audits:
        payload = by_id[row[0]]
        assert row[1:5] == (
            payload["brain_id"], payload["memory_id"], payload["agent_id"],
            payload["action"],
        )
        assert row[5] == payload["event_at_ms"]
        assert row[6] == timeline_day_for(
            payload["event_at_ms"], "UTC"
        ), "importer derives the day in the configured timezone"
        assert row[7] == canonical(payload["detail"])
    # Both events of the hard-deleted memory survive without a memory row:
    hard = [r for r in audits if r[2] in hard_deleted_ids]
    assert len(hard) == 2
    assert {r[4] for r in hard} == {"remember", "hard_delete"}


def test_lexical_search_finds_imported_content(imported) -> None:
    """Distinctive live terms resolve; the soft-deleted row matches nothing."""
    artifact = imported["artifact"]
    live = [p for p in artifact["memories"] if p["deleted_at_ms"] is None]
    soft = next(p for p in artifact["memories"] if p["deleted_at_ms"] is not None)
    with_content = [p for p in live if p["content"].strip()]

    # One term from a live memory's content, one from a live summary:
    content_payload = with_content[0]
    summary_payload = next(
        p for p in live if p is not content_payload and p["summary"].strip()
    )
    content_term = max(content_payload["content"].split(), key=len).strip(".,")
    summary_term = max(summary_payload["summary"].split(), key=len).strip(".,")

    clock = FakeClock(EXPORTED_AT_MS)
    for brain_id in {p["brain_id"] for p in live}:
        retriever = HybridMemoryRetriever(
            imported["factory"], brain_id=brain_id, clock=clock,
            force_vector_backend="numpy",
        )
        for term, payload in ((content_term, content_payload),
                              (summary_term, summary_payload)):
            if payload["brain_id"] != brain_id:
                continue
            found = {
                r.memory_id
                for r in retriever.search(
                    query_text=term,
                    query_vector=embedder_default(),
                )
            }
            assert payload["memory_id"] in found, (
                f"{term!r} must find {payload['memory_id']}, got {found}"
            )

    # The soft-deleted row matches nothing, even for ITS distinctive content:
    soft_brain = soft["brain_id"]
    retriever = HybridMemoryRetriever(
        imported["factory"], brain_id=soft_brain, clock=clock,
        force_vector_backend="numpy",
    )
    distinctive = max(soft["summary"].split(), key=len).strip(".,")
    assert soft["memory_id"] not in {
        r.memory_id
        for r in retriever.search(
            query_text=distinctive, query_vector=embedder_default()
        )
    }


def test_reembedded_vector_profile(imported) -> None:
    """Every blob is q4: 2560 bytes, float32[640], the FakeEmbedder default."""
    default_blob = embedder_default().values.astype("<f4", copy=False).tobytes()
    assert len(default_blob) == 2560
    with imported["factory"].connect(read_only=True) as con:
        rows = con.connection.execute(
            "SELECT memory_id, embedding, profile_id FROM memories"
        ).fetchall()
    assert len(rows) == 5
    for memory_id, blob, profile_id in rows:
        assert len(blob) == 2560, memory_id
        decoded = np.frombuffer(blob, dtype="<f4")
        assert decoded.shape == (640,), memory_id
        assert decoded.dtype == np.float32, memory_id
        assert bytes(decoded.tobytes()) == default_blob, (
            "importer re-embeds under the active profile, artifact carries none"
        )
        assert profile_id == "q4"


def test_import_run_records_the_artifact_identity(imported) -> None:
    """The run row pins export_id + artifact hash + exact counters."""
    artifact = imported["artifact"]
    report = imported["report"]
    assert report.status == "completed"
    assert report.export_id == artifact["manifest"]["export_id"]
    assert report.artifact_sha256 == PINNED_SHA256
    expected = (
        artifact["manifest"]["memory_count"]
        + artifact["manifest"]["audit_count"]
    )
    assert expected == 17
    assert report.imported_count == expected
    assert report.skipped_count == 0
    assert report.last_committed_seq == artifact["trailer"]["last_seq"] == 17

    with imported["factory"].connect(read_only=True) as con:
        run = con.connection.execute(
            "SELECT export_id, artifact_sha256, status, imported_count,"
            " skipped_count, failed_count, last_committed_seq FROM import_runs"
        ).fetchone()
    assert run[0] == artifact["manifest"]["export_id"]
    assert run[1] == PINNED_SHA256
    assert run[2] == "completed"
    assert run[3] == expected
    assert run[4] == 0 and run[5] == 0
    assert run[6] == artifact["trailer"]["last_seq"]


def embedder_default():
    """The FakeEmbedder e1 vector (its default for every document/query)."""
    return FakeEmbedder().default
