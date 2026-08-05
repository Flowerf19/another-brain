"""TASK-074: CLI recent/admin commands against the real SQLite stack.

FAST by construction and unmarked: the CLI's ``_NullBudgets`` means these
paths never construct the tokenizer-backed validator and never touch the
model cache or network. The store is seeded exactly the way the CLI opens it
(bootstrap + migrate + register_profile over ``BRAIN_DATA_DIR``), then rows
are inserted via ``SQLiteMemoryRepository.store`` with a deterministic
embedding vector — no embedder is involved, mirroring ``tests.unit.conftest``
patterns.

Each scenario drives ``cli.main([...])`` with a monkeypatched environment so
``AppConfig.from_env`` resolves to the temp data dir; audit assertions read
``audit_events`` through stdlib ``sqlite3``.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from another_brain import cli
from another_brain.domain.retention import expires_at_ms_for
from another_brain.services.sql.connection import SQLiteConnectionFactory
from another_brain.services.sql.migrations import migrate
from another_brain.services.sql.profile import register_profile
from tests.unit.conftest import FakeClock, unit_vector

BASE_MS = 4_100_000_000_000  # 2099-12-03 UTC — far future, so seeded rows stay
# live (expires_at > now) relative to any real clock; the CLI uses its own clock.
BRAIN_ID = "test-brain"
DAY = "2099-12-03"


def _seed_store(tmp_path: Path, clock: FakeClock) -> list[str]:
    """Open the store exactly like the CLI does and append three memories.

    Returns the memory ids oldest-first; created_at is 1h apart.
    """
    factory = SQLiteConnectionFactory(tmp_path / "data" / "brain.sqlite3")
    factory.bootstrap()
    migrate(factory.db_path)
    register_profile(factory)

    from another_brain.domain.models import MemoryRecord
    from another_brain.services.sql.repository import SQLiteMemoryRepository

    repo = SQLiteMemoryRepository(factory, brain_id=BRAIN_ID, clock=clock)
    ids: list[str] = []
    for index in range(3):
        memory_id = f"mem-{index}"
        ids.append(memory_id)
        repo.store(
            MemoryRecord(
                memory_id=memory_id,
                brain_id=BRAIN_ID,
                agent_id="seed-agent",
                topic=f"topic {index}",
                catalog="note" if index else "general",
                summary=f"summary number {index}",
                content="",
                timeline_day=DAY,
                period_start_ms=None,
                period_end_ms=None,
                created_at_ms=BASE_MS + index * 3_600_000,
                updated_at_ms=BASE_MS + index * 3_600_000,
                importance=3 + (index % 3),
                expires_at_ms=expires_at_ms_for(3 + (index % 3), BASE_MS),
                deleted_at_ms=None,
                metadata={},
                profile_id="q4",
                record_version=1,
                embedding=unit_vector(),
            )
        )
    return ids


@pytest.fixture
def cli_env(monkeypatch, tmp_path):
    monkeypatch.setenv("BRAIN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("BRAIN_MODEL_CACHE_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("BRAIN_ID", BRAIN_ID)
    monkeypatch.setenv("TIMELINE_TIMEZONE", "UTC")
    for key in ("MCP_HTTP_HOST", "MCP_HTTP_PORT"):
        monkeypatch.delenv(key, raising=False)
    return tmp_path


def _audit_rows(db_path: Path) -> list[tuple]:
    with sqlite3.connect(db_path) as con:
        return con.execute(
            "SELECT memory_id, agent_id, action FROM audit_events"
            " ORDER BY event_at_ms, event_id"
        ).fetchall()


def test_recent_lists_seeded_rows_newest_first_without_content(cli_env, capsys):
    ids = _seed_store(cli_env, FakeClock())

    assert cli.main(["recent", "--limit", "5"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]
    assert [line.split()[1] for line in lines] == list(reversed(ids))
    assert all(DAY in line for line in lines)
    assert all("summary number" in line for line in lines)
    assert all("content" not in line for line in lines)


def test_recent_default_limit_and_empty_store(cli_env, capsys):
    assert cli.main(["recent"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "no memories" in out


def test_admin_restore_unknown_id_is_typed_error(cli_env, capsys):
    _seed_store(cli_env, FakeClock())

    assert cli.main(["admin", "restore", "no-such-id"]) == cli.EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "not_found" in captured.err


def test_admin_restore_arms_forgotten_memory(cli_env, capsys):
    ids = _seed_store(cli_env, FakeClock())

    # forget mem-0 first: restore only applies to a soft-deleted row still
    # inside its grace window (a live row is NOT_FOUND by design).
    assert cli.main(["admin", "restore", ids[0]]) == cli.EXIT_ERROR
    capsys.readouterr()
    clock = FakeClock(BASE_MS)

    factory = SQLiteConnectionFactory(cli_env / "data" / "brain.sqlite3")
    from another_brain.services.sql.repository import SQLiteMemoryRepository

    repo = SQLiteMemoryRepository(factory, brain_id=BRAIN_ID, clock=clock)
    assert repo.soft_delete(ids[0]) is not None

    assert cli.main(["admin", "restore", ids[0]]) == cli.EXIT_OK
    restored = capsys.readouterr().out
    assert "restored" in restored
    assert "expires" in restored

    db_path = cli_env / "data" / "brain.sqlite3"
    with sqlite3.connect(db_path) as con:
        row = con.execute(
            "SELECT deleted_at_ms, expires_at_ms FROM memories WHERE memory_id = ?",
            (ids[0],),
        ).fetchone()
    assert row[0] is None, "restore must clear deleted_at"
    # Restore re-arms the TTL from the CLI's real clock: the new expiry must
    # sit ~90 days (importance 3) in the future of "now".
    assert row[1] > int(time.time() * 1000) + 60 * 86_400_000

    audits = _audit_rows(db_path)
    assert ("cli-admin", "restore") in {(a[1], a[2]) for a in audits}


def test_admin_hard_delete_removes_memory(cli_env, capsys):
    ids = _seed_store(cli_env, FakeClock())

    assert cli.main(["admin", "hard-delete", ids[0]]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "hard-deleted" in out
    assert "memory" not in out  # stdout carries the report line only

    db_path = cli_env / "data" / "brain.sqlite3"
    with sqlite3.connect(db_path) as con:
        count = con.execute(
            "SELECT COUNT(*) FROM memories WHERE memory_id = ?", (ids[0],)
        ).fetchone()[0]
    assert count == 0, "hard-delete must remove the row"

    audits = _audit_rows(db_path)
    assert ("cli-admin", "hard_delete") in {(a[1], a[2]) for a in audits}
    assert ids[0] in {a[0] for a in audits}, "audit history survives hard-delete"