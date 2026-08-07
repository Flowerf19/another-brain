"""TASK-097: a single Ctrl-C exits ``serve`` quietly with status 130.

Regression test for the leaked shutdown traceback (anyio stdin-reader worker
blocked on the pipe; interpreter shutdown hung joining it). Drives the repo's
console script — the one beside ``sys.executable``, so a stale globally
installed copy cannot mask the code under test — through the MCP ``initialize``
handshake as the readiness probe, sends SIGINT, and asserts fast exit, code
130 (POSIX; Windows kills hard, so the assertion branches), and no
``Traceback`` on stderr.

Uses the pinned q4 model from the shared default cache — run
``another-brain model pull`` first. Marked ``slow``; CI runs the fast suite.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from another_brain.config import AppConfig
from another_brain.services.embedding.model_installer import is_installed

pytestmark = pytest.mark.slow

BRAIN_ID = "sigint-e2e"
READY_TIMEOUT_SECONDS = 30.0
EXIT_TIMEOUT_SECONDS = 6.0

_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "sigint-e2e", "version": "1.0.0"},
    },
}


def _skip_if_unavailable() -> tuple[Path, AppConfig]:
    """Console script + pinned q4 profile, or skip with the repo's message.

    Prefers the script beside ``sys.executable`` (the repo venv) over PATH:
    a stale globally installed copy must not mask the code under test.
    """
    config = AppConfig.from_env()
    if not is_installed(config.model_cache_dir, verify_files=False):
        pytest.skip("the pinned q4 profile is not installed; run `another-brain model pull`")
    beside = Path(sys.executable).parent / "another-brain"
    script = beside if os.path.exists(beside) else shutil.which("another-brain")
    if script is None:
        pytest.skip("the `another-brain` console script was not found on PATH")
    return Path(script), config


def _serve_env(config: AppConfig, data_dir: Path) -> dict[str, str]:
    """Isolated data home + shared read-only model cache, vec fallback on.

    ``BRAIN_DISABLE_SQLITE_VEC`` skips loading the sqlite-vec extension so
    startup is fast and does not depend on the platform wheel.
    """
    env = dict(os.environ)
    env["BRAIN_DATA_DIR"] = str(data_dir)
    env["BRAIN_MODEL_CACHE_DIR"] = str(config.model_cache_dir)
    env["BRAIN_ID"] = BRAIN_ID
    env["TIMELINE_TIMEZONE"] = "UTC"
    env["BRAIN_DISABLE_SQLITE_VEC"] = "1"
    return env


def _wait_serving(proc: subprocess.Popen) -> None:
    """Complete the MCP initialize handshake; raises if the server never starts.

    Proves the server is fully serving (runtime built, transport up) before
    the signal is sent, so the test exercises the real serve loop.
    """
    assert proc.stdin and proc.stdout
    proc.stdin.write(json.dumps(_INITIALIZE) + "\n")
    proc.stdin.flush()
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            break  # EOF: server died during startup
        if '"id":1' in line and '"result"' in line:
            return
    raise AssertionError(
        "server did not answer the initialize handshake before timeout; "
        f"stderr so far: {proc.stderr.read()[:2000]}"
    )


def _send_sigint(proc: subprocess.Popen) -> None:
    """SIGINT to the child; Windows has no kill(2), so branch there."""
    if sys.platform == "win32":
        # CTRL_C_EVENT needs a console; CTRL_BREAK_EVENT works without one.
        sig = getattr(signal, "CTRL_BREAK_EVENT", signal.SIGINT)
        proc.send_signal(sig)
    else:
        proc.send_signal(signal.SIGINT)


def test_serve_exits_quietly_on_sigint(tmp_path):
    """One Ctrl-C: exit 130 within seconds, no traceback on stderr."""
    script, config = _skip_if_unavailable()
    data_dir = tmp_path / "data"

    proc = subprocess.Popen(
        [str(script), "serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=_serve_env(config, data_dir),
    )
    try:
        _wait_serving(proc)  # fully serving before the signal

        started = time.monotonic()
        _send_sigint(proc)
        try:
            returncode = proc.wait(timeout=EXIT_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
            raise AssertionError(
                "serve did not exit within a few seconds of one SIGINT; the "
                "anyio worker thread blocked on the pipe was still being "
                f"joined. stderr: {proc.stderr.read()[:2000]}"
            ) from None

        elapsed = time.monotonic() - started
        assert elapsed < EXIT_TIMEOUT_SECONDS, f"exit took {elapsed:.1f}s"

        if sys.platform == "win32":
            # Windows kills hard; the code 130 convention is POSIX-only.
            assert returncode != 0
        else:
            assert returncode == 130, (
                f"expected conventional 130 (128+SIGINT), got {returncode}"
            )

        stderr = proc.stderr.read()
        assert "Traceback" not in stderr, (
            f"shutdown leaked a traceback on stderr:\n{stderr[-2000:]}"
        )
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
