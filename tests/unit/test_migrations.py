"""TASK-049: migration runner — exclusive transactional apply, ledger
checksum verification, concurrent creators, crash rollback, fail-fast."""
from __future__ import annotations

import multiprocessing as mp
import sqlite3

import pytest

from another_brain.errors import MigrationError
from another_brain.services.sql import migrations
from another_brain.services.sql.schema import DDL_V1_STATEMENTS, SCHEMA_VERSION


@pytest.fixture
def db_path(tmp_path):
    from another_brain.services.sql.connection import SQLiteConnectionFactory

    path = tmp_path / "brain.sqlite3"
    SQLiteConnectionFactory(path).bootstrap()
    return path


def _ledger(con: sqlite3.Connection) -> dict[int, str]:
    return {
        version: checksum
        for version, checksum in con.execute(
            "SELECT version, checksum FROM schema_migrations"
        )
    }


class TestApply:
    def test_fresh_database_migrates_to_v1(self, db_path):
        assert migrations.migrate(db_path) == SCHEMA_VERSION
        raw = sqlite3.connect(str(db_path))
        try:
            assert raw.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
            assert _ledger(raw) == {1: migrations._checksum_for(1)}
            tables = {
                r[0] for r in raw.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                    " AND name NOT LIKE 'memory_fts_%'"
                )
            }
            assert "memories" in tables and "audit_events" in tables
        finally:
            raw.close()

    def test_second_migrate_is_idempotent(self, db_path):
        migrations.migrate(db_path)
        assert migrations.migrate(db_path) == SCHEMA_VERSION
        raw = sqlite3.connect(str(db_path))
        try:
            assert len(_ledger(raw)) == 1  # no duplicate ledger row
        finally:
            raw.close()


class TestFailFast:
    def test_newer_schema_refused(self, db_path):
        raw = sqlite3.connect(str(db_path))
        raw.execute(f"PRAGMA user_version={SCHEMA_VERSION + 5}")
        raw.commit()
        raw.close()
        with pytest.raises(MigrationError, match="newer"):
            migrations.migrate(db_path)

    def test_tampered_ledger_checksum_refused(self, db_path):
        migrations.migrate(db_path)
        raw = sqlite3.connect(str(db_path))
        raw.execute("UPDATE schema_migrations SET checksum='deadbeef' WHERE version=1")
        raw.commit()
        raw.close()
        with pytest.raises(MigrationError, match="ledger mismatch"):
            migrations.migrate(db_path)

    def test_ddl_drift_changes_expected_checksum(self, db_path, monkeypatch):
        migrations.migrate(db_path)
        # the frozen DDL changed without a version bump -> ledger mismatch
        monkeypatch.setitem(
            migrations.MIGRATIONS, 1, DDL_V1_STATEMENTS + ["CREATE INDEX extra ON memories(created_at_ms)"]
        )
        with pytest.raises(MigrationError, match="ledger mismatch"):
            migrations.migrate(db_path)


class TestCrashRollback:
    def test_failed_middle_migration_rolls_back_everything(self, db_path, monkeypatch):
        # simulate a v2 migration whose second statement fails
        monkeypatch.setattr(migrations, "SCHEMA_VERSION", 2)
        monkeypatch.setitem(
            migrations.MIGRATIONS, 1, DDL_V1_STATEMENTS
        )
        monkeypatch.setitem(
            migrations.MIGRATIONS, 2, ["CREATE TABLE v2_table(x)", "THIS IS NOT SQL"]
        )
        with pytest.raises(sqlite3.OperationalError):
            migrations.migrate(db_path)
        raw = sqlite3.connect(str(db_path))
        try:
            assert raw.execute("PRAGMA user_version").fetchone()[0] == 0
            tables = {
                r[0] for r in raw.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert "schema_migrations" not in tables  # ledger rolled back too
            assert "memories" not in tables  # v1 DDL rolled back too
        finally:
            raw.close()
        # once the bad migration is gone, the same file migrates cleanly
        monkeypatch.setattr(migrations, "SCHEMA_VERSION", 1)
        monkeypatch.setitem(migrations, "MIGRATIONS", {1: DDL_V1_STATEMENTS})
        assert migrations.migrate(db_path) == 1


def _spawn_migrate(path: str, outq) -> None:
    from another_brain.services.sql import migrations as m

    try:
        outq.put(("ok", m.migrate(path)))
    except Exception as exc:  # noqa: BLE001
        outq.put(("error", repr(exc)))


def test_concurrent_creators_converge(db_path):
    """Four processes race to migrate one absent database; all converge."""
    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    procs = [
        ctx.Process(target=_spawn_migrate, args=(str(db_path), queue))
        for _ in range(4)
    ]
    for p in procs:
        p.start()
    results = [queue.get(timeout=60) for _ in procs]
    for p in procs:
        p.join(timeout=30)
    assert all(code == "ok" and version == SCHEMA_VERSION for code, version in results), results
    raw = sqlite3.connect(str(db_path))
    try:
        assert len(_ledger(raw)) == 1  # exactly one creator applied v1
    finally:
        raw.close()
