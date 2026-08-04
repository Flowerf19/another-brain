"""Cross-platform SQLite connection/bootstrap."""
from __future__ import annotations

import hashlib
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from filelock import FileLock

from ..errors import ConfigError
from .schema import DDL, SCHEMA_VERSION

SCHEMA_CHECKSUM = hashlib.sha256(DDL.encode("utf-8")).hexdigest()


class SQLiteConnectionFactory:
    def __init__(self, path: Path):
        self.path = Path(path)

    def bootstrap(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = FileLock(str(self.path) + ".schema.lock", timeout=30)
        with lock:
            fresh = not self.path.exists() or self.path.stat().st_size == 0
            connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
            try:
                connection.execute("PRAGMA busy_timeout=5000")
                connection.execute("PRAGMA foreign_keys=ON")
                if fresh:
                    connection.execute("PRAGMA page_size=16384")
                page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
                if page_size != 16_384:
                    raise ConfigError(
                        f"database page_size is {page_size}, expected 16384; "
                        "move the database aside and import it into a fresh profile"
                    )
                journal = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                if str(journal).lower() != "wal":
                    raise ConfigError(f"could not enable SQLite WAL mode: {journal!r}")
                connection.execute("PRAGMA synchronous=NORMAL")
                current = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if current > SCHEMA_VERSION:
                    raise ConfigError(
                        f"database schema {current} is newer than supported {SCHEMA_VERSION}"
                    )
                if current == 0:
                    try:
                        # executescript issues an implicit COMMIT before its script;
                        # put BEGIN inside the script so DDL + migration marker share
                        # the same explicit transaction on Windows and POSIX.
                        connection.executescript("BEGIN EXCLUSIVE;\n" + DDL)
                        connection.execute(
                            "INSERT OR REPLACE INTO schema_migrations VALUES (?,?,?)",
                            (SCHEMA_VERSION, SCHEMA_CHECKSUM, int(time.time() * 1000)),
                        )
                        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                        connection.execute("COMMIT")
                    except Exception:
                        if connection.in_transaction:
                            connection.execute("ROLLBACK")
                        raise
                row = connection.execute(
                    "SELECT checksum FROM schema_migrations WHERE version=?",
                    (SCHEMA_VERSION,),
                ).fetchone()
                if row is None or row[0] != SCHEMA_CHECKSUM:
                    raise ConfigError("SQLite schema checksum mismatch")
            finally:
                connection.close()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=NORMAL")
            yield connection
        finally:
            connection.close()
