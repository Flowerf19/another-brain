"""TASK-071: import-jsonl CLI end to end against the installed console script.

Drives the installed ``another-brain`` console script with ``import-jsonl``
over an isolated data home (``tmp_path``), using the pinned q4 model from the
shared default cache. Proves the real product path:

- a valid v1 envelope imports: stdout report carries the completed status and
  the fixture's memory/audit counts, the database rows land, ``import_runs``
  is ``completed``, and a second run is a noop;
- an envelope that fails validation exits non-zero, names the violation on
  stderr, and writes nothing (no ``import_runs`` row).

The envelope test is deliberately FAST: envelope validation happens before
any embedding, so no model is needed. The end-to-end test skips when the
console script is not on PATH (or beside ``sys.executable``) or the pinned
q4 profile is missing from the default model cache — run
``another-brain model pull`` first.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from another_brain.config import AppConfig
from another_brain.services.embedding.model_installer import is_installed

pytestmark = pytest.mark.slow

ROOT = Path(__file__).resolve().parents[2]
VALID_FIXTURE = ROOT / "tests" / "fixtures" / "jsonl-v1" / "valid-basic.jsonl"
INVALID_FIXTURE = (
    ROOT / "tests" / "fixtures" / "jsonl-v1" / "invalid-bad-rolling-hash.jsonl"
)

BRAIN_ID = "import-e2e"
CLI_TIMEOUT_SECONDS = 300.0  # the first import pays the cold ONNX load


def _console_script() -> Path:
    """The console script on PATH, else the venv bin beside sys.executable."""
    script = shutil.which("another-brain")
    if script is None:
        script = str(Path(sys.executable).parent / "another-brain")
    assert os.path.exists(script), (
        f"the `another-brain` console script was not found: {script}"
    )
    return Path(script)


def _run(script: Path, config: AppConfig, data_dir: Path, *args: str):
    """Run the console script in an isolated data home + shared model cache."""
    env = dict(os.environ)
    env["BRAIN_DATA_DIR"] = str(data_dir)
    env["BRAIN_MODEL_CACHE_DIR"] = str(config.model_cache_dir)
    env["BRAIN_ID"] = BRAIN_ID
    env["TIMELINE_TIMEZONE"] = "UTC"
    return subprocess.run(
        [str(script), *args],
        capture_output=True,
        text=True,
        timeout=CLI_TIMEOUT_SECONDS,
        env=env,
        cwd=ROOT,
    )


def _row_counts(db_path: Path) -> tuple[int, int, list[tuple]]:
    """memories, audit_events rows, and every import_runs row."""
    with sqlite3.connect(db_path) as con:
        memories = con.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        audits = con.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        runs = con.execute(
            "SELECT export_id, status, imported_count, skipped_count"
            " FROM import_runs ORDER BY export_id"
        ).fetchall()
    return memories, audits, runs


def test_import_valid_basic_end_to_end(tmp_path):
    """Model needed: embedding happens during the import, so this is slow."""
    config = AppConfig.from_env()
    if not is_installed(config.model_cache_dir, verify_files=False):
        pytest.skip(
            "the pinned q4 profile is not installed; run `another-brain model pull`"
        )
    script = _console_script()
    data_dir = tmp_path / "data"

    first = _run(script, config, data_dir, "import-jsonl", str(VALID_FIXTURE))
    assert first.returncode == 0, f"first import failed: {first.stderr}"
    # valid-basic.jsonl carries 2 memory lines and 1 audit line; the fixture's
    # expires_at_ms values are still in the future relative to any real clock,
    # so both memories embed and all three rows are imported.
    assert "status completed" in first.stdout, first.stdout
    assert "imported 3, skipped 0" in first.stdout, first.stdout
    assert "artifact " in first.stdout, first.stdout

    memories, audits, runs = _row_counts(data_dir / "brain.sqlite3")
    assert memories == 2, f"expected 2 memories, got {memories}"
    assert audits == 1, f"expected 1 audit event, got {audits}"
    assert runs == [("01234567-89ab-cdef-0123-456789abcdef", "completed", 3, 0)], runs

    second = _run(script, config, data_dir, "import-jsonl", str(VALID_FIXTURE))
    assert second.returncode == 0, f"second import failed: {second.stderr}"
    assert "status noop" in second.stdout, second.stdout
    assert "imported 3, skipped 0" in second.stdout, second.stdout

    memories, audits, runs = _row_counts(data_dir / "brain.sqlite3")
    assert memories == 2, f"second import must not duplicate: {memories}"
    assert audits == 1, f"second import must not duplicate: {audits}"
    assert runs == [("01234567-89ab-cdef-0123-456789abcdef", "completed", 3, 0)], runs


def test_invalid_envelope_fails_fast(tmp_path):
    """No marker: envelope validation happens before any embedding/model load."""
    script = _console_script()
    config = AppConfig.from_env()
    data_dir = tmp_path / "data"

    result = _run(script, config, data_dir, "import-jsonl", str(INVALID_FIXTURE))
    assert result.returncode != 0, "an invalid envelope must exit non-zero"
    assert "rolling_sha256" in result.stderr, result.stderr

    # Envelope rejection happens before import_runs is ever written.
    db_path = data_dir / "brain.sqlite3"
    assert db_path.exists(), "bootstrap/migrate still create the database"
    with sqlite3.connect(db_path) as con:
        runs = con.execute("SELECT COUNT(*) FROM import_runs").fetchone()[0]
    assert runs == 0, f"envelope errors must write no import_runs row, got {runs}"
