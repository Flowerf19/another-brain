"""Schema migration runner (TASK-049).

- ``PRAGMA user_version`` is the single source of the current schema version;
  ``schema_migrations`` is the checksummed ledger of applied versions.
- All pending migrations run inside ONE ``BEGIN EXCLUSIVE`` transaction:
  DDL, ledger inserts, and ``user_version`` commit together or roll back
  together (crash rollback is free — SQLite DDL is transactional).
- Cross-process safety via the same ``<db>.schema.lock`` the bootstrap
  writer uses, so concurrent creators serialize; the second process sees
  ``user_version == SCHEMA_VERSION`` and only verifies the ledger.
- Fail-fast: a database whose ``user_version`` is newer than this build, or
  whose ledger checksum does not match the frozen DDL, is refused — never
  silently reopened or half-migrated.

Call ``factory.bootstrap()`` before ``migrate()`` (page size must be set
before the first schema object; the flows are deliberately separate).
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from filelock import FileLock

from another_brain.config import SQLITE_PAGE_SIZE
from another_brain.errors import MigrationError
from another_brain.services.sql.connection import SCHEMA_LOCK_SUFFIX
from another_brain.services.sql.schema import DDL_V1_STATEMENTS, SCHEMA_VERSION, checksum

# version -> single statements; append-only (a new version adds a new entry).
MIGRATIONS: dict[int, list[str]] = {SCHEMA_VERSION: DDL_V1_STATEMENTS}


def migrate(db_path: Path) -> int:
    """Bring ``db_path`` to :data:`SCHEMA_VERSION`; returns the final version."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(db_path) + SCHEMA_LOCK_SUFFIX):
        raw = sqlite3.connect(str(db_path))
        try:
            page_size = raw.execute("PRAGMA page_size").fetchone()[0]
            if page_size != SQLITE_PAGE_SIZE:
                raise MigrationError(
                    f"database page size is {page_size}, expected"
                    f" {SQLITE_PAGE_SIZE}; call bootstrap() before migrate()"
                )
            current = raw.execute("PRAGMA user_version").fetchone()[0]
            if current > SCHEMA_VERSION:
                raise MigrationError(
                    f"database schema v{current} is newer than this build"
                    f" (v{SCHEMA_VERSION}); refusing to open"
                )
            if current == SCHEMA_VERSION:
                _verify_ledger(raw)
                return current
            _apply(raw, current)
            return SCHEMA_VERSION
        finally:
            raw.close()


def _apply(raw: sqlite3.Connection, from_version: int) -> None:
    """Apply every pending migration in one exclusive, rollback-safe tx."""
    raw.execute("BEGIN EXCLUSIVE")
    try:
        for version in range(from_version + 1, SCHEMA_VERSION + 1):
            for statement in MIGRATIONS[version]:
                raw.execute(statement)
            raw.execute(
                "INSERT INTO schema_migrations(version, checksum, applied_at)"
                " VALUES (?, ?, ?)",
                (version, checksum(), int(time.time() * 1000)),
            )
        raw.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        raw.commit()
    except BaseException:  # noqa: BLE001 - rollback on any failure, incl. crash
        raw.rollback()
        raise


def _verify_ledger(raw: sqlite3.Connection) -> None:
    """The recorded ledger must exactly match the frozen migration set.

    The single source of truth is :func:`schema.checksum` (the exact frozen
    DDL text); the runner never recomputes a parallel hash.
    """
    expected = {SCHEMA_VERSION: checksum()}
    actual = {
        version: checksum
        for version, checksum in raw.execute(
            "SELECT version, checksum FROM schema_migrations"
        )
    }
    if actual != expected:
        raise MigrationError(
            f"schema ledger mismatch: expected {expected}, found {actual}"
        )
