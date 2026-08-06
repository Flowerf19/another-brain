"""TASK-084: ``another-brain doctor`` — the diagnostic report.

The doctor must run correctly in every degradation state (fresh machine,
missing model, corrupt DB), never load the embedding model, never download
anything, and never mutate the real database. These tests drive the CLI
with a temp BRAIN_DATA_DIR/BRAIN_MODEL_CACHE_DIR profile; all heavy
assertions live behind the config seam, never against a real user profile.
"""
from __future__ import annotations

import importlib.metadata
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from another_brain import cli
from another_brain.config import AppConfig
from another_brain.domain.models import EmbeddingProfile
from another_brain.services.embedding.model_manifest import MODEL_MANIFEST
from another_brain.services.sql.connection import SQLiteConnectionFactory
from another_brain.services.sql.migrations import migrate
from another_brain.services.sql.profile import register_profile


@pytest.fixture
def profile_env(tmp_path, monkeypatch):
    """Temp BRAIN_DATA_DIR/BRAIN_MODEL_CACHE_DIR so doctor never touches the
    real user profile; the data dir exists (like a fresh install)."""
    monkeypatch.setenv("BRAIN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("BRAIN_MODEL_CACHE_DIR", str(tmp_path / "models"))
    # Isolate from a CI forced-fallback pass: these tests assert the default
    # vec-capable report, so the env must not leak in.
    monkeypatch.delenv("BRAIN_DISABLE_SQLITE_VEC", raising=False)
    return tmp_path


def _run_doctor():
    """Invoke the doctor through the real CLI entry point."""
    return cli.main(["doctor"])


def _statuses(out: str) -> dict[str, str]:
    """Map item name -> status, tolerant of alignment padding."""
    import re

    return {m.group(2): m.group(1) for m in re.finditer(r"\[\s*(\w+)\]\s+(\w+)", out)}


# ---------------------------------------------------------------- fresh empty


class TestFreshProfile:
    def test_report_renders_with_warns_and_ok_exit(self, profile_env, capsys):
        """Fresh empty profile: model and database warn, nothing fails,
        exit EXIT_OK. The report is one line per item with a [status]
        prefix and a summary line."""
        assert _run_doctor() == cli.EXIT_OK
        out = capsys.readouterr().out
        assert out.startswith("[")
        # statuses and names, tolerant of alignment padding
        statuses = _statuses(out)
        assert statuses.get("model") == "warn"
        assert statuses.get("database") == "warn"
        assert statuses.get("platform") == "ok"
        assert statuses.get("probe") == "ok"
        assert "another-brain model pull" in out  # the actionable model hint
        assert "no database yet" in out
        assert "summary:" in out

    def test_fresh_profile_writes_nothing_to_real_dirs(self, profile_env):
        """The doctor must never create the DB or any file in the real
        profile dirs — only the isolated probe touches temp space."""
        _run_doctor()
        data_dir = Path(profile_env) / "data"
        models_dir = Path(profile_env) / "models"
        # The doctor creates neither dir nor any file inside them — a fresh
        # profile stays untouched (the fixture only sets the env override).
        assert not data_dir.exists()
        assert not models_dir.exists()


# ------------------------------------------------------------ after bootstrap


@pytest.fixture
def bootstrapped_profile(profile_env):
    """A real store created through the app's own bootstrap + migrate +
    register_profile — exactly what the server open path does."""
    config = AppConfig.from_env()
    factory = SQLiteConnectionFactory(config.database_path)
    factory.bootstrap()
    migrate(config.database_path)
    register_profile(factory)
    return profile_env


class TestBootstrappedProfile:
    def test_database_and_probe_ok(self, bootstrapped_profile, capsys):
        assert _run_doctor() == cli.EXIT_OK
        out = capsys.readouterr().out
        statuses = _statuses(out)
        assert statuses.get("database") == "ok"
        assert statuses.get("probe") == "ok"
        assert "integrity ok" in out
        assert "foreign keys ok" in out
        assert "schema v1" in out
        assert "journal wal" in out
        assert "page_size 16384" in out
        assert "sqlite-vec loaded" in out
        assert "insert/read/delete + FTS hit ok" in out
        assert "all checks passed" in out

    def test_probe_warns_when_fallback_forced(
        self, bootstrapped_profile, monkeypatch, capsys
    ):
        """BRAIN_DISABLE_SQLITE_VEC=1: the probe reports the NumPy fallback
        with the env var named as the reason; a warn, not a failure."""
        monkeypatch.setenv("BRAIN_DISABLE_SQLITE_VEC", "1")
        assert _run_doctor() == cli.EXIT_OK
        out = capsys.readouterr().out
        assert _statuses(out).get("probe") == "warn"
        assert "disabled by BRAIN_DISABLE_SQLITE_VEC" in out

    def test_real_db_is_not_mutated_by_doctor(self, bootstrapped_profile):
        """Read-only contract: running doctor over a real store changes
        nothing — same row counts, same schema, same file set."""
        data_dir = Path(bootstrapped_profile) / "data"
        db_path = data_dir / "brain.sqlite3"
        with SQLiteConnectionFactory(db_path).connect() as con:
            memory_count = con.connection.execute(
                "SELECT count(*) FROM memories"
            ).fetchone()[0]
            schema_after_open = sorted(
                row[0]
                for row in con.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            )
        before = sorted(p.name for p in data_dir.iterdir())
        assert _run_doctor() == cli.EXIT_OK
        after = sorted(p.name for p in data_dir.iterdir())
        # WAL/shm files appear and disappear with any open connection (mine
        # included), so compare only the stable main artifacts: the DB file
        # and the schema lock.
        stable = lambda names: [  # noqa: E731
            n for n in names if not n.endswith(("-wal", "-shm"))
        ]
        assert stable(before) == stable(after)
        with SQLiteConnectionFactory(db_path).connect() as con:
            assert con.connection.execute(
                "SELECT count(*) FROM memories"
            ).fetchone()[0] == memory_count
            assert schema_after_open == sorted(
                row[0]
                for row in con.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            )
        # no journal/cleanup side effects from doctor on the stable artifacts
        assert stable(before) == stable(after)


# --------------------------------------------------------------- corrupt DB


class TestCorruptDatabase:
    def test_garbage_over_db_file_is_fail_and_exit_error(self, profile_env, capsys):
        data_dir = Path(profile_env) / "data"
        data_dir.mkdir(exist_ok=True)
        (data_dir / "brain.sqlite3").write_bytes(b"not a sqlite file at all")
        assert _run_doctor() == cli.EXIT_ERROR
        out = capsys.readouterr().out
        assert "[fail] database" in out
        assert "file is not a database" in out
        assert "one or more checks FAILED" in out


# ------------------------------------------------------------ platform fail


class TestPlatformFail:
    def test_unsupported_platform_is_fail_and_exit_error(
        self, profile_env, monkeypatch, capsys
    ):
        from another_brain.services import system

        unsupported = system.SystemReport(
            os_family="linux",
            arch="s390x",
            libc="glibc",
            macos_version="",
            python_version="3.12.0",
            tier="unsupported",
            expect_sqlite_vec=False,
            reason="untested platform",
        )
        monkeypatch.setattr(system, "current_system", lambda: unsupported)
        assert _run_doctor() == cli.EXIT_ERROR
        out = capsys.readouterr().out
        assert "[fail] platform" in out
        assert "untested platform" in out


# -------------------------------------------------- never loads the model


class TestNeverLoadsModel:
    def test_doctor_never_imports_onnxruntime(self, profile_env):
        """The whole doctor run must not import onnxruntime (or the
        tokenizer it pulls in) — checked in a fresh subprocess so other test
        modules cannot have polluted sys.modules."""
        import subprocess

        code = (
            "import sys; sys.argv=['another-brain','doctor'];"
            "import another_brain.cli as c;"
            "rc = c.main(['doctor']);"
            "bad = [m for m in ('onnxruntime','tokenizers') if m in sys.modules];"
            "assert not bad, bad;"
            "sys.exit(rc)"
        )
        env = {
            **__import__("os").environ,
            "BRAIN_DATA_DIR": str(Path(profile_env) / "data"),
            "BRAIN_MODEL_CACHE_DIR": str(Path(profile_env) / "models"),
        }
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, env=env
        )
        assert result.returncode == 0, result.stderr
        assert "onnxruntime" not in result.stderr

    def test_doctor_never_imports_numpy_in_process(self, profile_env):
        """In-process variant: numpy is imported by other test modules, so
        this asserts the doctor service module itself does not pull it in
        when imported fresh in a subprocess."""
        import subprocess

        code = (
            "import sys;"
            "import another_brain.services.doctor;"
            "bad = [m for m in ('numpy',) if m in sys.modules];"
            "assert not bad, bad"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
