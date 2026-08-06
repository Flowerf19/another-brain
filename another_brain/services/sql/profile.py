"""Active embedding-profile registration (TASK-067).

``memories.profile_id`` is a foreign key into ``embedding_profiles``, and the
migration runner deliberately seeds no rows — it owns frozen DDL, not runtime
facts. Registration therefore belongs to service open: the row is derived from
the locked manifest, so the database records the exact contract its vectors
were written under.

This is the write half of the gate :mod:`another_brain.services.sql.health`
reads. A stored row that disagrees with the manifest is refused rather than
overwritten: rows already embedded under the old contract stay searchable only
until a re-embedding migration completes, and silently re-pointing the profile
would strand them behind a claim that they match.
"""
from __future__ import annotations

import time

from another_brain.domain.models import EmbeddingProfile
from another_brain.errors import StorageError
from another_brain.services.embedding.model_manifest import MODEL_MANIFEST
from another_brain.services.sql.connection import SQLiteConnectionFactory

_INSERT = (
    "INSERT INTO embedding_profiles(profile_id, model_repo, model_revision,"
    " variant, dimension, dtype, normalized, tokenizer_sha256, config_sha256,"
    " prompt_utf8_sha256, query_prompt, input_version, created_at_ms)"
    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


def register_profile(factory: SQLiteConnectionFactory) -> str:
    """Ensure the manifest profile is the registered one; returns its id.

    Idempotent across processes: concurrent servers race on the same
    ``BEGIN IMMEDIATE``, and the loser sees the winner's row and verifies it.
    """
    profile = EmbeddingProfile.from_manifest(
        MODEL_MANIFEST, created_at_ms=int(time.time() * 1000)
    )
    with factory.connect() as con:
        raw = con.connection
        raw.execute("BEGIN IMMEDIATE")
        try:
            rows = raw.execute(
                "SELECT profile_id, model_revision, input_version, dimension"
                " FROM embedding_profiles"
            ).fetchall()
            if not rows:
                raw.execute(
                    _INSERT,
                    (
                        profile.profile_id, profile.model_repo, profile.model_revision,
                        profile.variant, profile.dimension, profile.dtype,
                        int(profile.normalized), profile.tokenizer_sha256,
                        profile.config_sha256, profile.prompt_utf8_sha256,
                        profile.query_prompt, profile.input_version,
                        profile.created_at_ms,
                    ),
                )
            else:
                _assert_matches(rows, profile)
            raw.commit()
        except BaseException:
            raw.rollback()
            raise
    return profile.profile_id


def _assert_matches(rows: list, profile: EmbeddingProfile) -> None:
    if len(rows) > 1:
        found = ", ".join(sorted(r[0] for r in rows))
        raise StorageError(
            f"database registers {len(rows)} embedding profiles ({found});"
            " a re-embedding migration is incomplete, so search would mix"
            " contracts — finish it before starting the server"
        )
    stored_id, revision, input_version, dimension = rows[0]
    actual = (stored_id, revision, input_version, dimension)
    expected = (
        profile.profile_id, profile.model_revision,
        profile.input_version, profile.dimension,
    )
    if actual != expected:
        raise StorageError(
            f"database was written under embedding profile {actual}, this build"
            f" uses {expected}; its vectors are not comparable, so re-embed the"
            " store instead of pointing it at a different model"
        )
