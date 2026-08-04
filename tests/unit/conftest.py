"""Shared SQL fixtures for the services/sql unit tests."""
from __future__ import annotations

import pytest

from another_brain.services.sql.connection import SQLiteConnectionFactory
from another_brain.services.sql.migrations import migrate

PROFILE_SQL = (
    "INSERT INTO embedding_profiles(profile_id, model_repo, model_revision,"
    " variant, dimension, dtype, normalized, tokenizer_sha256, config_sha256,"
    " prompt_utf8_sha256, query_prompt, input_version, created_at_ms)"
    " VALUES ('q4', 'repo', 'rev', 'q4', 640, 'float32', 1, ?, ?, ?, 'q', 2, 1)"
)


@pytest.fixture
def sql_factory(tmp_path) -> SQLiteConnectionFactory:
    """Bootstrapped, migrated v1 database with the locked q4 profile row."""
    factory = SQLiteConnectionFactory(tmp_path / "brain.sqlite3")
    factory.bootstrap()
    migrate(factory.db_path)
    with factory.connect() as con:
        con.connection.execute(PROFILE_SQL, ("a" * 64, "a" * 64, "a" * 64))
        con.connection.commit()
    return factory
