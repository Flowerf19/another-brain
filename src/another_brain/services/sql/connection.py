"""SQLite connection factory — three flows, one locked contract (TASK-047).

- ``bootstrap()`` — cross-process locked writer that sets ``page_size``
  BEFORE the first schema object exists, then flips persistent WAL. No
  tables are created here (the migration runner owns DDL, TASK-049) and the
  lock file is the same one the runner will take, so bootstrap + migrate
  serialize across processes.
- ``connect()`` — normal read/write connection: local PRAGMAs
  (``foreign_keys``, ``busy_timeout``, ``synchronous``) then verifies the
  database state (WAL journal + locked page size). Fails fast on a database
  that was never bootstrapped or has a foreign page size.
- ``connect(read_only=True)`` — ``mode=ro`` URI + ``query_only``; inspects
  without setting any write PRAGMA and never creates the file (a missing
  database is a typed :class:`DatabaseOpenError`).

Every connection is a :class:`Connection` wrapper with guaranteed close
(context manager + idempotent ``close()``), narrow sqlite-vec extension
loading (extension loading enabled only around the load call), and a
per-connection ``vec_loaded`` flag that drives the NumPy fallback decision
at retrieval time — the fallback is per connection, never global.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from filelock import FileLock

from another_brain.config import (
    SQLITE_BUSY_TIMEOUT_MS,
    SQLITE_PAGE_SIZE,
    SQLITE_SYNCHRONOUS,
)
from another_brain.errors import DatabaseOpenError, StorageError

SCHEMA_LOCK_SUFFIX = ".schema.lock"


class SQLiteConnectionFactory:
    """Opens bootstrapped/read-write/read-only connections to one database."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)

    @property
    def db_path(self) -> Path:
        return self._db_path

    def bootstrap(self) -> None:
        """One-time locked bootstrap; idempotent, crash-safe, no DDL."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self._db_path) + SCHEMA_LOCK_SUFFIX):
            con = sqlite3.connect(str(self._db_path))
            try:
                con.execute(f"PRAGMA page_size={SQLITE_PAGE_SIZE}")
                mode = con.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            finally:
                con.close()
            if mode != "wal":
                raise StorageError(
                    f"journal_mode could not be set to WAL, got {mode!r}"
                )

    def connect(self, *, read_only: bool = False) -> Connection:
        if read_only:
            if not self._db_path.exists():
                raise DatabaseOpenError(
                    f"read-only open failed: database does not exist: {self._db_path}"
                )
            raw = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
        else:
            raw = sqlite3.connect(str(self._db_path))
        connection = Connection(raw, read_only=read_only)
        try:
            connection._configure()
        except Exception:
            connection.close()
            raise
        return connection


class Connection:
    """Guaranteed-close wrapper; carries the per-connection vec capability."""

    def __init__(self, raw: sqlite3.Connection, *, read_only: bool) -> None:
        self._raw = raw
        self._read_only = read_only
        self._closed = False
        self._vec_loaded = False
        self._vec_error: str | None = None

    # -- surface -----------------------------------------------------------

    @property
    def connection(self) -> sqlite3.Connection:
        if self._closed:
            raise StorageError("connection is closed")
        return self._raw

    @property
    def read_only(self) -> bool:
        return self._read_only

    @property
    def vec_loaded(self) -> bool:
        return self._vec_loaded

    @property
    def vec_load_error(self) -> str | None:
        return self._vec_error

    def close(self) -> None:
        if not self._closed:
            self._raw.close()
            self._closed = True

    def __enter__(self) -> "Connection":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- internals ----------------------------------------------------------

    def _configure(self) -> None:
        if self._read_only:
            self._raw.execute("PRAGMA query_only=ON")
            return
        self._raw.execute("PRAGMA foreign_keys=ON")
        self._raw.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        self._raw.execute(f"PRAGMA synchronous={SQLITE_SYNCHRONOUS}")
        self._verify_state()

    def _verify_state(self) -> None:
        mode = self._raw.execute("PRAGMA journal_mode").fetchone()[0]
        if mode != "wal":
            raise StorageError(
                f"database not bootstrapped: journal_mode is {mode!r},"
                " expected 'wal'; call bootstrap() first"
            )
        page_size = self._raw.execute("PRAGMA page_size").fetchone()[0]
        if page_size != SQLITE_PAGE_SIZE:
            raise StorageError(
                f"unexpected page size {page_size}, expected {SQLITE_PAGE_SIZE};"
                " refusing to open a foreign database"
            )

    def load_vec(self) -> bool:
        """Load the sqlite-vec extension narrowly; idempotent, never raises.

        Enables extension loading only around the load call. Records the
        per-connection capability so retrieval can choose the NumPy exact
        fallback without any global state.
        """
        if self._vec_loaded:
            return True
        try:
            import sqlite_vec

            path = sqlite_vec.loadable_path()
            self._raw.enable_load_extension(True)
            try:
                self._raw.load_extension(path)
            finally:
                self._raw.enable_load_extension(False)
        except Exception as exc:  # noqa: BLE001 - capability probe never raises
            self._vec_error = str(exc)
            return False
        self._vec_loaded = True
        return True
