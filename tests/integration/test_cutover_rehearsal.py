"""TASK-075 rehearsal: end-to-end cutover from the real legacy artifact.

Drives the INSTALLED ``another-brain`` console script exactly as an operator
would cut over: import the pinned ``main-export-v1.jsonl`` artifact into an
isolated fresh profile (tmp BRAIN_DATA_DIR, shared read-only model cache),
then operate the resulting store over MCP stdio bound to export-brain-a,
restart the server, and finally confirm the artifact itself is untouched.

Every artifact fact (memory ids, summaries, audit day, per-brain event
counts) is READ from the fixture at test time — nothing is hardcoded except
the pinned SHA-256. The soft-deleted memory (22222222-…) and the
hard-deleted memory (33333333-…, audit-only) come from the fixture too.

Skips when the console script is not on PATH (or beside ``sys.executable``)
or the pinned q4 profile is missing from the default model cache — run
``another-brain model pull`` first. Marked ``slow``; CI runs the fast suite.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp_types import Implementation

from another_brain.config import AppConfig
from another_brain.services.embedding.model_installer import is_installed

pytestmark = pytest.mark.slow

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "tests" / "fixtures" / "jsonl-v1" / "main-export-v1.jsonl"
PINNED_SHA256 = "abb4d40c1ddf74b17f1315f33e9fe32500fa52b2d4137aa659af01b5548bca0a"

BRAIN_ID = "export-brain-a"
CLIENT_NAME = "cutover-agent"
READ_TIMEOUT_SECONDS = 180.0  # the first search pays the cold ONNX load
CLI_TIMEOUT_SECONDS = 300.0   # the import pays the cold ONNX load


def _artifact() -> dict:
    """Parse the fixture: manifest, memory payloads, audit payloads, trailer."""
    lines = [json.loads(line) for line in ARTIFACT.read_text().splitlines()]
    manifest, trailer = lines[0], lines[-1]
    memories = [l["payload"] for l in lines[1:-1] if l["kind"] == "memory"]
    audits = [l["payload"] for l in lines[1:-1] if l["kind"] == "audit"]
    assert manifest["kind"] == "manifest" and trailer["kind"] == "trailer"
    return {"manifest": manifest, "trailer": trailer,
            "memories": memories, "audits": audits}


def _skip_if_unavailable() -> tuple[Path, AppConfig]:
    """Console script + pinned q4 profile, or skip with the repo's message."""
    config = AppConfig.from_env()
    if not is_installed(config.model_cache_dir, verify_files=False):
        pytest.skip("the pinned q4 profile is not installed; run `another-brain model pull`")
    script = shutil.which("another-brain")
    if script is None:
        script = str(Path(sys.executable).parent / "another-brain")
    if not os.path.exists(script):
        pytest.skip("the `another-brain` console script was not found on PATH")
    return Path(script), config


def _env(script: Path, config: AppConfig, data_dir: Path) -> dict[str, str]:
    """Isolated data home + shared read-only model cache, bound to brain A."""
    env = dict(os.environ)
    env["BRAIN_DATA_DIR"] = str(data_dir)
    env["BRAIN_MODEL_CACHE_DIR"] = str(config.model_cache_dir)
    env["BRAIN_ID"] = BRAIN_ID
    env["TIMELINE_TIMEZONE"] = "UTC"
    return env


def _result(r: Any, name: str) -> dict[str, Any]:
    """Structured tool result (snake_case in v2), else the text fallback."""
    if getattr(r, "is_error", False) or getattr(r, "structured_content", None) is None:
        text = "".join(c.text for c in r.content if getattr(c, "type", None) == "text")
        raise AssertionError(f"tool {name} failed: {text or r}")
    return r.structured_content


def _spawn(script: Path, config: AppConfig, data_dir: Path) -> StdioServerParameters:
    return StdioServerParameters(
        command=str(script), args=[], env=_env(script, config, data_dir),
    )


async def _call(session, name: str, args: dict | None = None) -> dict[str, Any]:
    return _result(await session.call_tool(name, args or {}), name)


async def test_cutover_rehearsal_from_external_artifact(tmp_path):
    script, config = _skip_if_unavailable()
    artifact = _artifact()
    data_dir = tmp_path / "data"

    # ---- PHASE 1: import into the isolated fresh profile -------------------
    env = _env(script, config, data_dir)
    proc = subprocess.run(
        [str(script), "import-jsonl", str(ARTIFACT)],
        capture_output=True, text=True, timeout=CLI_TIMEOUT_SECONDS,
        env=env, cwd=ROOT,
    )
    assert proc.returncode == 0, f"import failed: {proc.stderr}"
    assert "status completed" in proc.stdout, proc.stdout
    assert "imported 17, skipped 0" in proc.stdout, proc.stdout
    assert PINNED_SHA256[:12] in proc.stdout, proc.stdout

    db_path = data_dir / "brain.sqlite3"
    assert db_path.exists()
    with sqlite3.connect(db_path) as con:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        assert con.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 5
        assert con.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 12
        run = con.execute(
            "SELECT export_id, artifact_sha256, status, imported_count,"
            " skipped_count FROM import_runs"
        ).fetchone()
    assert run is not None and run[0] == artifact["manifest"]["export_id"]
    assert run[1] == PINNED_SHA256
    assert run[2] == "completed" and run[3] == 17 and run[4] == 0

    # Facts for the stdio phases, read from the fixture:
    live_a = [p for p in artifact["memories"]
              if p["brain_id"] == BRAIN_ID and p["deleted_at_ms"] is None]
    soft = next(p for p in artifact["memories"] if p["deleted_at_ms"] is not None)
    memory_ids_a = {p["memory_id"] for p in artifact["memories"]
                    if p["brain_id"] == BRAIN_ID}
    hard_ids = ({a["memory_id"] for a in artifact["audits"]}
                - {p["memory_id"] for p in artifact["memories"]})
    audit_day = datetime.fromtimestamp(
        artifact["audits"][0]["event_at_ms"] / 1000, tz=timezone.utc
    ).date().isoformat()
    assert all(
        datetime.fromtimestamp(a["event_at_ms"] / 1000, tz=timezone.utc).date().isoformat()
        == audit_day for a in artifact["audits"]
    )
    audits_a = [a for a in artifact["audits"] if a["brain_id"] == BRAIN_ID]
    summary_payload = max(live_a, key=lambda p: len(p["summary"].split()))
    term = max(summary_payload["summary"].split(), key=len).strip(".,")
    expected_ids = {p["memory_id"] for p in live_a}
    hard_events = [a for a in audits_a if a["memory_id"] in hard_ids]
    assert len(hard_events) == 2
    assert {a["action"] for a in hard_events} == {"remember", "hard_delete"}

    # ---- PHASE 2: post-cutover operation over stdio ------------------------
    params = _spawn(script, config, data_dir)
    client_info = Implementation(name=CLIENT_NAME, version="1.0.0")
    recent_after_restart: list[str] = []

    async with stdio_client(params) as (read, write):
        async with ClientSession(
            read, write, read_timeout_seconds=READ_TIMEOUT_SECONDS,
            client_info=client_info,
        ) as session:
            await session.initialize()

            health = await _call(session, "health")
            assert health["status"] == "ok"
            assert health["brain_id"] == BRAIN_ID
            assert health["embedding_state"] == "not_loaded"
            assert health["storage"]["schema_ok"] is True

            found = await _call(session, "search", {"query": term})
            assert any(
                r["memory_id"] == summary_payload["memory_id"]
                for r in found["results"]
            ), f"{term!r} must find {summary_payload['memory_id']}, got {found}"

            gone = await _call(session, "get",
                               {"memory_id": soft["memory_id"]})
            assert gone["found"] is False, (
                f"soft-deleted {soft['memory_id']} must be invisible"
            )

            audit = await _call(session, "audit", {"day": audit_day})
            assert audit["count"] == len(audits_a), (
                f"expected {len(audits_a)} events for {BRAIN_ID} on {audit_day},"
                f" got {audit['count']}"
            )
            by_id = {e["event_id"]: e for e in audit["events"]}
            for event in audits_a:
                assert event["event_id"] in by_id, event["event_id"]
                assert by_id[event["event_id"]]["memory_id"] == event["memory_id"]
                assert by_id[event["event_id"]]["action"] == event["action"]
            hard_in_audit = {
                e["event_id"] for e in audit["events"] if e["memory_id"] in hard_ids
            }
            assert hard_in_audit == {a["event_id"] for a in hard_events}, (
                f"remember/hard_delete for {hard_ids} must be in the audit trail,"
                f" got {hard_in_audit}"
            )
    # session closed -> server process exits

    # ---- RESPAWN: imported state survives restart; bound-brain isolation ----
    async with stdio_client(params) as (read, write):
        async with ClientSession(
            read, write, read_timeout_seconds=READ_TIMEOUT_SECONDS,
            client_info=client_info,
        ) as session:
            await session.initialize()

            again = await _call(session, "search", {"query": term})
            again_ids = {r["memory_id"] for r in again["results"]}
            assert summary_payload["memory_id"] in again_ids, again

            recent = await _call(session, "recent", {"limit": 100})
            recent_ids = [r["memory_id"] for r in recent["results"]]
            assert sorted(recent_ids) == sorted(expected_ids), (
                f"recent must expose only brain A's {len(expected_ids)} live"
                f" memories, got {recent_ids}"
            )

    # ---- PHASE 3: rollback posture — the artifact file is untouched ---------
    assert hashlib.sha256(ARTIFACT.read_bytes()).hexdigest() == PINNED_SHA256, (
        "import must never mutate the source artifact"
    )
