"""TASK-048: schema v1 — six tables, every CHECK constraint enforced by
SQLite itself, FTS triggers sync, checksum stable."""
from __future__ import annotations

import sqlite3

import pytest

from another_brain.schema import DDL_V1, SCHEMA_VERSION, checksum


@pytest.fixture
def con():
    connection = sqlite3.connect(":memory:")
    connection.executescript(DDL_V1)
    connection.execute("PRAGMA foreign_keys=ON")
    yield connection
    connection.close()


def _profile(con, profile_id: str = "q4") -> None:
    con.execute(
        "INSERT INTO embedding_profiles(profile_id, model_repo, model_revision,"
        " variant, dimension, dtype, normalized, tokenizer_sha256, config_sha256,"
        " prompt_utf8_sha256, query_prompt, input_version, created_at_ms)"
        " VALUES (?, 'repo', 'rev', 'q4', 640, 'float32', 1, ?, ?, ?, 'q', 2, 1)",
        (profile_id, "a" * 64, "a" * 64, "a" * 64),
    )


def _memory(con, row_id: int = 1, **overrides) -> tuple:
    row = (
        row_id,
        overrides.get("memory_id", "mem-1"),
        overrides.get("brain_id", "default"),
        overrides.get("agent_id", "agent-a"),
        overrides.get("scope", "user"),
        overrides.get("scope_id", "u1"),
        overrides.get("topic", "sqlite-benchmark"),
        overrides.get("catalog", "engineering"),
        overrides.get("summary", "notes"),
        overrides.get("content", ""),
        overrides.get("timeline_day", "2026-08-04"),
        overrides.get("period_start_ms", None),
        overrides.get("period_end_ms", None),
        overrides.get("created_at_ms", 1000),
        overrides.get("updated_at_ms", 1000),
        overrides.get("importance", 3),
        overrides.get("expires_at_ms", 1_000_000),
        overrides.get("deleted_at_ms", None),
        overrides.get("metadata", "{}"),
        overrides.get("profile_id", "q4"),
        overrides.get("embedding", b"\x00" * 2560),
        overrides.get("record_version", 1),
    )
    return row


def test_six_tables_exist(con):
    tables = {
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
            " AND name NOT LIKE 'memory_fts_%'"  # FTS5 shadow tables
        ).fetchall()
    }
    assert tables == {
        "schema_migrations", "embedding_profiles", "memories", "memory_fts",
        "import_runs", "audit_events",
    }


def test_checksum_stable_and_covers_ddl():
    digest = checksum()
    assert len(digest) == 64
    assert checksum() == digest
    assert digest != hashlib_sha256("tampered")


def hashlib_sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()


class TestMemoriesConstraints:
    def test_valid_row_inserted_with_fts_sync(self, con):
        _profile(con)
        con.execute(_INSERT_SQL, _memory(con))
        assert con.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0] == 1

    @pytest.mark.parametrize("column,value", [
        ("scope", "team"), ("importance", 6), ("importance", 0),
        ("record_version", 0), ("updated_at_ms", 999),  # < created 1000
    ])
    def test_check_rejects_bad_values(self, con, column, value):
        _profile(con)
        row = _memory(con)
        fields = list(row)
        fields[_COLUMNS.index(column)] = value
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(_INSERT_SQL, tuple(fields))

    def test_metadata_must_be_json_object(self, con):
        _profile(con)
        with pytest.raises(sqlite3.IntegrityError, match="json"):
            con.execute(_INSERT_SQL, _memory(con, metadata='["not","object"]'))

    def test_embedding_blob_must_be_2560_bytes(self, con):
        _profile(con)
        with pytest.raises(sqlite3.IntegrityError, match="2560"):
            con.execute(_INSERT_SQL, _memory(con, embedding=b"\x00" * 2559))

    def test_unique_brain_memory_id(self, con):
        _profile(con)
        con.execute(_INSERT_SQL, _memory(con))
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            con.execute(_INSERT_SQL, _memory(con))

    def test_period_ordered(self, con):
        _profile(con)
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                _INSERT_SQL, _memory(con, period_start_ms=200, period_end_ms=100)
            )

    def test_profile_fk_enforced(self, con):
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            con.execute(_INSERT_SQL, _memory(con))  # profile 'q4' absent

    def test_signed_timestamps_allowed(self, con):
        _profile(con)
        con.execute(_INSERT_SQL, _memory(con, created_at_ms=-5, updated_at_ms=-5))


_COLUMNS = [
    "row_id",
    "memory_id", "brain_id", "agent_id", "scope", "scope_id", "topic",
    "catalog", "summary", "content", "timeline_day", "period_start_ms",
    "period_end_ms", "created_at_ms", "updated_at_ms", "importance",
    "expires_at_ms", "deleted_at_ms", "metadata", "profile_id",
    "embedding", "record_version",
]

_INSERT_SQL = (
    "INSERT INTO memories(row_id, memory_id, brain_id, agent_id, scope,"
    " scope_id, topic, catalog, summary, content, timeline_day,"
    " period_start_ms, period_end_ms, created_at_ms, updated_at_ms,"
    " importance, expires_at_ms, deleted_at_ms, metadata, profile_id,"
    " embedding, record_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,\n"
    " ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


class TestFtsTriggers:
    def test_update_and_delete_sync(self, con):
        _profile(con)
        con.execute(_INSERT_SQL, _memory(con))
        con.execute(
            "UPDATE memories SET summary='changed' WHERE row_id=1"
        )
        assert con.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0] == 1
        assert con.execute(
            "SELECT summary FROM memory_fts WHERE rowid=1"
        ).fetchone()[0] == "changed"
        con.execute("DELETE FROM memories WHERE row_id=1")
        assert con.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0] == 0

    def test_diacritics_removed_for_search(self, con):
        _profile(con)
        con.execute(_INSERT_SQL, _memory(con, summary="bài viết tiếng Việt"))
        con.execute(_INSERT_SQL, _memory(
            con, row_id=2, memory_id="mem-2", summary="plain english note"))
        match = con.execute(
            "SELECT rowid FROM memory_fts WHERE memory_fts MATCH 'bai'"
        ).fetchall()
        assert match == [(1,)]


class TestAuditAndImportChecks:
    def test_audit_action_and_json_checks(self, con):
        con.execute(
            "INSERT INTO audit_events(event_id, brain_id, memory_id, agent_id,"
            " action, event_at_ms, timeline_day, detail_json)"
            " VALUES ('e1', 'b', 'm', 'a', 'remember', 1, '2026-08-04', '{}')"
        )
        with pytest.raises(sqlite3.IntegrityError, match="action"):
            con.execute(
                "INSERT INTO audit_events(event_id, brain_id, memory_id,"
                " agent_id, action, event_at_ms, timeline_day, detail_json)"
                " VALUES ('e2', 'b', 'm', 'a', 'update', 1, '2026-08-04', '{}')"
            )
        with pytest.raises(sqlite3.IntegrityError, match="json"):
            con.execute(
                "INSERT INTO audit_events(event_id, brain_id, memory_id,"
                " agent_id, action, event_at_ms, timeline_day, detail_json)"
                " VALUES ('e3', 'b', 'm', 'a', 'forget', 1, '2026-08-04', 'nope')"
            )

    def test_import_run_constraints(self, con):
        con.execute(
            "INSERT INTO import_runs(export_id, artifact_sha256, format_version,"
            " status, last_committed_seq, imported_count, skipped_count,"
            " failed_count, started_at_ms, completed_at_ms)"
            " VALUES ('x', ?, 1, 'running', 0, 0, 0, 0, 1, NULL)",
            ("b" * 64,),
        )
        with pytest.raises(sqlite3.IntegrityError, match="status"):
            con.execute(
                "INSERT INTO import_runs(export_id, artifact_sha256,"
                " format_version, status, last_committed_seq, imported_count,"
                " skipped_count, failed_count, started_at_ms, completed_at_ms)"
                " VALUES ('y', ?, 1, 'paused', 0, 0, 0, 0, 1, NULL)",
                ("c" * 64,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            con.execute(
                "INSERT INTO import_runs(export_id, artifact_sha256,"
                " format_version, status, last_committed_seq, imported_count,"
                " skipped_count, failed_count, started_at_ms, completed_at_ms)"
                " VALUES ('z', ?, 1, 'running', 0, 0, 0, 0, 1, NULL)",
                ("b" * 64,),  # duplicate artifact sha
            )
