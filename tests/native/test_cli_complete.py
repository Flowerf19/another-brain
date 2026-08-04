from __future__ import annotations

import json

import pytest

import another_brain.cli as cli
from another_brain.config import AppConfig
from another_brain.domain.models import MemoryRecord
from another_brain.storage.repository import SQLiteRepository


def configure(monkeypatch, tmp_path):
    data = tmp_path / "data"
    model = tmp_path / "model"
    monkeypatch.setenv("ANOTHER_BRAIN_DATA_DIR", str(data))
    monkeypatch.setenv("ANOTHER_BRAIN_MODEL_DIR", str(model))
    monkeypatch.setenv("BRAIN_ID", "cli-test")
    return data, model


def vector():
    return (1.0,) + (0.0,) * 639


def test_cli_version(capsys):
    with pytest.raises(SystemExit) as exited:
        cli.main(["--version"])
    assert exited.value.code == 0
    assert capsys.readouterr().out.strip() == "0.11.1"


def test_cli_model_status_is_read_only_and_json(monkeypatch, tmp_path, capsys):
    _, model = configure(monkeypatch, tmp_path)
    assert cli.main(["model", "status"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output == {"installed": False, "path": str(model)}
    assert not model.exists()


def test_cli_model_pull_calls_verified_installer(monkeypatch, tmp_path, capsys):
    _, model = configure(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "install_model", lambda path: path)
    assert cli.main(["model", "pull"]) == 0
    assert json.loads(capsys.readouterr().out) == {"installed": True, "path": str(model)}


def test_cli_doctor_bootstraps_native_database(monkeypatch, tmp_path, capsys):
    data, _ = configure(monkeypatch, tmp_path)
    assert cli.main(["doctor"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "ok"
    assert output["version"] == "0.11.1"
    assert output["platform_native"] is True
    assert output["database"]["fts5"] is True
    assert (data / "brain.sqlite3").is_file()


def test_cli_recent_prints_preview_without_content(monkeypatch, tmp_path, capsys):
    data, _ = configure(monkeypatch, tmp_path)
    config = AppConfig.from_env()
    repository = SQLiteRepository(config.database_path, timezone=config.timeline_timezone)
    item = MemoryRecord.new(
        brain_id="cli-test", agent_id="pytest", scope="global", scope_id="",
        topic="cli-recent", summary="CLI recent preview.", content="MUST-NOT-PRINT",
        timezone=config.timeline_timezone,
    )
    repository.store(item, vector())
    assert cli.main(["recent", "--scope", "global", "--limit", "10"]) == 0
    output = capsys.readouterr().out
    assert "cli-recent" in output
    assert "CLI recent preview." in output
    assert "MUST-NOT-PRINT" not in output


def test_cli_admin_restore_and_hard_delete(monkeypatch, tmp_path, capsys):
    configure(monkeypatch, tmp_path)
    config = AppConfig.from_env()
    repository = SQLiteRepository(config.database_path, timezone=config.timeline_timezone)
    item = MemoryRecord.new(
        brain_id="cli-test", agent_id="pytest", scope="global", scope_id="",
        topic="cli-admin", summary="CLI admin lifecycle.", timezone=config.timeline_timezone,
    )
    repository.store(item, vector())
    repository.soft_delete(
        "cli-test", item.memory_id, now_ms=item.created_at_ms + 1, grace_ms=60_000
    )
    assert cli.main(["admin", "restore", item.memory_id]) == 0
    assert json.loads(capsys.readouterr().out)["restored"] is True
    assert cli.main(["admin", "hard-delete", item.memory_id]) == 0
    assert json.loads(capsys.readouterr().out)["deleted"] is True


def test_cli_expected_config_error_returns_two(monkeypatch, tmp_path, capsys):
    configure(monkeypatch, tmp_path)
    monkeypatch.setenv("MCP_HTTP_HOST", "0.0.0.0")
    assert cli.main(["doctor"]) == 2
    assert "loopback-only" in capsys.readouterr().err


@pytest.mark.parametrize("http,expected", [
    (False, ("stdio", {})),
    (True, ("streamable-http", {"host": "127.0.0.1", "port": 1905})),
])
def test_serve_dispatches_transport_without_platform_branch(monkeypatch, tmp_path, http, expected):
    config = AppConfig.from_env(
        {
            "ANOTHER_BRAIN_DATA_DIR": str(tmp_path / "data"),
            "ANOTHER_BRAIN_MODEL_DIR": str(tmp_path / "model"),
        }
    )
    calls = []

    class FakeServer:
        def run(self, transport="stdio", **kwargs):
            calls.append((transport, kwargs))

    monkeypatch.setattr(cli, "build_service", lambda value: object())
    monkeypatch.setattr("another_brain.mcp.server.build_mcp_server", lambda service: FakeServer())
    assert cli._serve(config, http=http) == 0
    assert calls == [expected]
