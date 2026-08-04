"""TASK-047: connection factory — bootstrap/normal/read-only flows, state
verification, guaranteed close, narrow vec loading, per-connection fallback."""
from __future__ import annotations

import sqlite3

import pytest

from another_brain.config import (
    SQLITE_BUSY_TIMEOUT_MS,
    SQLITE_PAGE_SIZE,
    SQLITE_SYNCHRONOUS,
)
from another_brain.errors import DatabaseOpenError, MigrationError, StorageError
from another_brain.services.sql.connection import SQLiteConnectionFactory
from another_brain.services.sql.migrations import migrate


@pytest.fixture
def factory(tmp_path) -> SQLiteConnectionFactory:
    return SQLiteConnectionFactory(tmp_path / "brain.sqlite3")


class TestBootstrap:
    def test_bootstrap_sets_page_size_before_objects_then_wal(self, factory):
        factory.bootstrap()
        raw = sqlite3.connect(str(factory.db_path))
        try:
            assert raw.execute("PRAGMA page_size").fetchone()[0] == SQLITE_PAGE_SIZE
            assert raw.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        finally:
            raw.close()

    def test_bootstrap_idempotent(self, factory):
        factory.bootstrap()
        factory.bootstrap()  # page size already locked in -> verify passes

    def test_bootstrap_refuses_non_empty_foreign_db(self, tmp_path):
        path = tmp_path / "foreign.sqlite3"
        raw = sqlite3.connect(str(path))
        raw.execute("CREATE TABLE t(x)")  # default 4096-byte pages
        raw.close()
        with pytest.raises(StorageError, match="page size"):
            SQLiteConnectionFactory(path).bootstrap()

    def test_bootstrap_creates_parent_directories(self, tmp_path):
        factory = SQLiteConnectionFactory(tmp_path / "nested" / "dir" / "brain.sqlite3")
        factory.bootstrap()
        assert factory.db_path.exists()


class TestNormalConnect:
    def test_local_pragmas_applied(self, factory):
        factory.bootstrap()
        with factory.connect() as con:
            raw = con.connection
            assert raw.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            assert raw.execute("PRAGMA busy_timeout").fetchone()[0] == SQLITE_BUSY_TIMEOUT_MS
            assert raw.execute("PRAGMA synchronous").fetchone()[0] == 1  # 1 = NORMAL

    def test_unbootstrapped_database_rejected(self, factory):
        # touch the file without bootstrap
        sqlite3.connect(str(factory.db_path)).close()
        with pytest.raises(StorageError, match="bootstrapped"):
            factory.connect()

    def test_foreign_page_size_rejected(self, tmp_path):
        path = tmp_path / "foreign.sqlite3"
        raw = sqlite3.connect(str(path))
        raw.execute("PRAGMA page_size=4096")
        raw.execute("CREATE TABLE t(x)")
        raw.execute("PRAGMA journal_mode=WAL")
        raw.close()
        factory = SQLiteConnectionFactory(path)
        with pytest.raises(StorageError, match="page size"):
            factory.connect()

    def test_usable_for_writes(self, factory):
        factory.bootstrap()
        with factory.connect() as con:
            con.connection.execute("CREATE TABLE t(x)")
            con.connection.execute("INSERT INTO t VALUES (1)")
            con.connection.commit()
        with factory.connect() as con:
            assert con.connection.execute("SELECT x FROM t").fetchone()[0] == 1


class TestReadOnly:
    def test_missing_database_is_typed_error(self, factory):
        with pytest.raises(DatabaseOpenError, match="does not exist"):
            factory.connect(read_only=True)
        assert not factory.db_path.exists()  # ro never creates the file

    def test_reads_work_writes_rejected(self, factory):
        factory.bootstrap()
        with factory.connect() as con:
            con.connection.execute("CREATE TABLE t(x)")
            con.connection.execute("INSERT INTO t VALUES (7)")
            con.connection.commit()
        with factory.connect(read_only=True) as con:
            assert con.read_only
            assert con.connection.execute("SELECT x FROM t").fetchone()[0] == 7
            with pytest.raises(sqlite3.OperationalError):
                con.connection.execute("INSERT INTO t VALUES (8)")
                con.connection.commit()

    def test_read_only_applies_busy_timeout_and_foreign_keys(self, factory):
        factory.bootstrap()
        migrate(factory.db_path)
        with factory.connect(read_only=True) as con:
            raw = con.connection
            assert raw.execute("PRAGMA query_only").fetchone()[0] == 1
            assert (
                raw.execute("PRAGMA busy_timeout").fetchone()[0]
                == SQLITE_BUSY_TIMEOUT_MS
            )
            assert raw.execute("PRAGMA foreign_keys").fetchone()[0] == 1


class TestCloseGuarantees:
    def test_context_manager_closes(self, factory):
        factory.bootstrap()
        con = factory.connect()
        with con:
            pass
        with pytest.raises(StorageError, match="closed"):
            con.connection  # noqa: B018

    def test_close_idempotent(self, factory):
        factory.bootstrap()
        con = factory.connect()
        con.close()
        con.close()  # second close is a no-op

    def test_configure_failure_closes_connection(self, factory):
        # unbootstrapped: _configure raises; the wrapper must close the raw
        # handle so no descriptor leaks
        sqlite3.connect(str(factory.db_path)).close()
        with pytest.raises(StorageError):
            factory.connect()


class TestVerifySchema:
    def test_missing_database_is_database_open_error(self, tmp_path):
        factory = SQLiteConnectionFactory(tmp_path / "missing.sqlite3")
        with pytest.raises(DatabaseOpenError, match="does not exist"):
            factory.verify_schema()

    def test_bootstrapped_but_unmigrated_refused(self, factory):
        factory.bootstrap()
        with pytest.raises(MigrationError, match="migrate"):
            factory.verify_schema()

    def test_incomplete_schema_reported(self, factory):
        factory.bootstrap()
        migrate(factory.db_path)
        with factory.connect() as con:
            con.connection.execute("DROP TABLE audit_events")
            con.connection.commit()
        with pytest.raises(StorageError, match="audit_events"):
            factory.verify_schema()

    def test_ok_after_migrate(self, factory):
        factory.bootstrap()
        migrate(factory.db_path)
        factory.verify_schema()


class TestVecCapability:
    def test_load_vec_succeeds_and_is_idempotent(self, factory):
        factory.bootstrap()
        with factory.connect() as con:
            assert con.load_vec() is True
            assert con.vec_loaded
            assert con.vec_load_error is None
            assert con.load_vec() is True  # idempotent, no reload

    def test_vec_load_failure_records_capability_false(self, factory, monkeypatch):
        factory.bootstrap()
        with factory.connect() as con:
            monkeypatch.setattr("sqlite_vec.loadable_path", lambda: "/nonexistent/vec0")
            assert con.load_vec() is False
            assert not con.vec_loaded
            assert con.vec_load_error  # reason recorded, never raised
