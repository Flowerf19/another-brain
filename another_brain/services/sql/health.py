"""SQLite storage health probe (TASK-065).

Answers ``health``/``doctor`` with schema, profile, extension, and optional
integrity state. Every path here is a pure read on a read-only connection:
the probe never writes, never migrates, and never loads the embedding model.

``integrity_ok`` is opt-in. ``PRAGMA integrity_check`` walks the whole
database, so a health call that ran it on every request would scale with
store size; ``deep=True`` belongs to ``doctor``, not to a liveness answer.

The stored profile is compared against the locked manifest because a
mismatch is exactly the state that must block mixed-profile search until
re-embedding completes — the same gate ``validate_profile`` enforces on the
write path.
"""
from __future__ import annotations

import sqlite3

from another_brain.protocols import StorageState
from another_brain.services.embedding.model_manifest import MODEL_MANIFEST
from another_brain.services.sql.connection import SQLiteConnectionFactory
from another_brain.services.sql.schema import SCHEMA_TABLES, SCHEMA_VERSION


class SQLiteHealthProbe:
    """``StorageHealthProbe`` over one database file."""

    def __init__(self, factory: SQLiteConnectionFactory) -> None:
        self._factory = factory

    def state(self, *, deep: bool = False) -> StorageState:
        """Schema/profile/extension state; ``deep`` adds the integrity check.

        A database that cannot be opened at all is reported as unhealthy
        rather than raised: health must answer even when storage is broken,
        which is precisely when it is asked.
        """
        try:
            with self._factory.connect(read_only=True) as con:
                raw = con.connection
                version = raw.execute("PRAGMA user_version").fetchone()[0]
                present = {
                    name
                    for (name,) in raw.execute(
                        "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
                    )
                }
                missing = SCHEMA_TABLES - present
                profile_id = self._stored_profile(raw)
                integrity_ok = self._integrity(raw) if deep else None
                vector_backend = "sqlite-vec" if con.load_vec() else "numpy"
        except Exception as exc:  # noqa: BLE001 — health reports, never raises
            return StorageState(
                schema_version=0,
                schema_ok=False,
                profile_id=None,
                profile_matches_manifest=False,
                vector_backend="unavailable",
                integrity_ok=False if deep else None,
                detail=f"storage unavailable: {exc}",
            )

        schema_ok = version == SCHEMA_VERSION and not missing
        detail = None
        if version != SCHEMA_VERSION:
            detail = f"schema version {version} != expected {SCHEMA_VERSION}"
        elif missing:
            detail = f"missing tables: {', '.join(sorted(missing))}"
        return StorageState(
            schema_version=version,
            schema_ok=schema_ok,
            profile_id=profile_id,
            profile_matches_manifest=profile_id == MODEL_MANIFEST.profile,
            vector_backend=vector_backend,
            integrity_ok=integrity_ok,
            detail=detail,
        )

    @staticmethod
    def _stored_profile(raw: sqlite3.Connection) -> str | None:
        """The single registered profile, or ``None`` when absent or mixed.

        More than one row means a re-embedding migration is in flight; that
        is not a profile the service can claim to be running under, so it
        reads the same as "no single profile" and fails the manifest match.
        """
        rows = raw.execute("SELECT profile_id FROM embedding_profiles LIMIT 2").fetchall()
        return rows[0][0] if len(rows) == 1 else None

    @staticmethod
    def _integrity(raw: sqlite3.Connection) -> bool:
        row = raw.execute("PRAGMA integrity_check").fetchone()
        return bool(row) and row[0] == "ok"
