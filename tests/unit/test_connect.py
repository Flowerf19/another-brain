"""TASK-093: ``another-brain connect`` — data-driven harness registry, JSON
upsert, wheel-bundled skill install, CLI wiring.

The service layer is exercised with tmp_path as a fake home and a recording
runner so nothing shells out; the CLI layer drives cli.main with a fake
home via HOME env (connect reads Path.home() at call time, never cached).
"""
from __future__ import annotations

import importlib.resources
import json
import subprocess
from pathlib import Path

import pytest

from another_brain import cli
from another_brain.errors import BrainError
from another_brain.services.harness_connect import (
    SERVER_ENTRY_JSON,
    SKILL_NAME,
    SKILL_RESOURCE_DIR,
    UnknownHarnessError,
    connect,
    detect_harnesses,
    install_skill,
    known_harnesses,
    upsert_server,
)


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Point the connect service at tmp_path; never the real $HOME."""
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def bundled_skill_text() -> str:
    """The exact bundled SKILL.md, straight from the wheel resources.

    Tests run from the source tree, where another_brain/skill/ does not exist
    (force-include only) — fall back to the repo-root skills copy, which is
    the identical content the wheel bundles.
    """
    try:
        return (
            importlib.resources.files(SKILL_RESOURCE_DIR)
            .joinpath("SKILL.md")
            .read_text()
        )
    except (ModuleNotFoundError, FileNotFoundError, OSError):
        return (Path(__file__).resolve().parents[2] / "skills" / "another-brain" / "SKILL.md").read_text()


class FakeRunner:
    """Records subprocess invocations; never executes anything."""

    def __init__(self, *, rc: int = 0, stdout: str = "") -> None:
        self.calls: list[list[str]] = []
        self.rc = rc
        self.stdout = stdout

    def __call__(self, cmd, **kwargs) -> subprocess.CompletedProcess:
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, self.rc, stdout=self.stdout, stderr="")


# ---------------------------------------------------------------- registry


class TestKnownHarnesses:
    def test_registry_has_all_five(self):
        assert known_harnesses() == (
            "claude-code",
            "codex",
            "cursor",
            "gemini-cli",
            "pi",
        )


# ---------------------------------------------------------------- detection


class TestDetect:
    def test_none_detected_in_empty_home(self, fake_home):
        assert detect_harnesses(fake_home) == ()

    def test_detection_is_dotdir_existence(self, fake_home):
        (fake_home / ".claude").mkdir()
        (fake_home / ".cursor").mkdir()
        (fake_home / ".pi").mkdir()
        assert detect_harnesses(fake_home) == ("claude-code", "cursor", "pi")

    def test_config_file_alone_does_not_detect(self, fake_home):
        # Detection keys on the harness dotdir; claude's real config file
        # (~/.claude.json) alone must not count as "installed".
        (fake_home / ".claude.json").write_text('{}')
        assert detect_harnesses(fake_home) == ()

    def test_explicit_home_beats_env(self, fake_home):
        other = fake_home / "other"
        (other / ".codex").mkdir(parents=True)
        assert detect_harnesses(other) == ("codex",)
        assert detect_harnesses(fake_home) == ()


# ------------------------------------------------------------ JSON upsert


class TestUpsertServer:
    def _write(self, path: Path, data) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data) + "\n")

    def test_creates_file_with_server_entry(self, fake_home):
        target = fake_home / ".cursor" / "mcp.json"
        message = upsert_server(target)
        data = json.loads(target.read_text())
        assert data["mcpServers"]["another-brain"] == {"command": "another-brain"}
        assert message == (
            f"wrote {target}: mcpServers.another-brain = {SERVER_ENTRY_JSON}"
        )
        assert target.read_text().endswith("\n")

    def test_preserves_unrelated_servers_and_keys(self, fake_home):
        target = fake_home / ".gemini" / "settings.json"
        self._write(
            target,
            {
                "existing-key": [1, 2],
                "mcpServers": {
                    "other": {"command": "other-tool", "args": ["-x"]},
                },
            },
        )
        upsert_server(target)
        data = json.loads(target.read_text())
        assert data["existing-key"] == [1, 2]
        assert data["mcpServers"]["other"] == {"command": "other-tool", "args": ["-x"]}
        assert data["mcpServers"]["another-brain"] == {"command": "another-brain"}

    def test_idempotent(self, fake_home):
        target = fake_home / ".cursor" / "mcp.json"
        upsert_server(target)
        upsert_server(target)
        data = json.loads(target.read_text())
        assert data == {"mcpServers": {"another-brain": {"command": "another-brain"}}}

    def test_invalid_existing_json_starts_from_empty(self, fake_home):
        target = fake_home / ".cursor" / "mcp.json"
        target.parent.mkdir(parents=True)
        target.write_text("{ not json ")
        upsert_server(target)
        data = json.loads(target.read_text())
        assert data["mcpServers"]["another-brain"] == {"command": "another-brain"}

    def test_non_object_json_starts_from_empty(self, fake_home):
        target = fake_home / ".cursor" / "mcp.json"
        target.parent.mkdir(parents=True)
        target.write_text("[1, 2]")
        upsert_server(target)
        data = json.loads(target.read_text())
        assert data == {"mcpServers": {"another-brain": {"command": "another-brain"}}}


# ----------------------------------------------------------- skill install


class TestSkillInstall:
    def test_lands_exact_bundled_content(self, fake_home):
        ok, path = install_skill(fake_home, ".claude/skills")
        assert ok
        assert path == str(fake_home / ".claude" / "skills" / SKILL_NAME)
        written = Path(path) / "SKILL.md"
        assert written.read_text() == bundled_skill_text()

    def test_rerun_never_nests(self, fake_home):
        install_skill(fake_home, ".cursor/skills")
        install_skill(fake_home, ".cursor/skills")
        target = fake_home / ".cursor" / "skills" / SKILL_NAME
        assert (target / "SKILL.md").is_file()
        assert not (target / SKILL_NAME).exists()
        assert (target / "SKILL.md").read_text() == bundled_skill_text()


# ----------------------------------------------------------------- connect


class TestConnect:
    def test_unknown_harness_raises_typed_error(self, fake_home):
        with pytest.raises(UnknownHarnessError) as exc:
            connect(["nope"], home=fake_home)
        assert isinstance(exc.value, BrainError)
        assert "nope" in str(exc.value)

    def test_claude_without_cli_returns_manual_result(self, fake_home, monkeypatch):
        monkeypatch.setattr(
            "another_brain.services.harness_connect.shutil.which", lambda _: None
        )
        (fake_home / ".claude").mkdir()
        result = connect(["claude-code"], home=fake_home)[0]
        assert result.registered == "manual"
        assert result.snippet == SERVER_ENTRY_JSON
        assert "claude" in result.messages[0]
        assert result.skill_installed is True

    def test_codex_without_cli_returns_manual_result(self, fake_home, monkeypatch):
        monkeypatch.setattr(
            "another_brain.services.harness_connect.shutil.which", lambda _: None
        )
        (fake_home / ".codex").mkdir()
        result = connect(["codex"], home=fake_home)[0]
        assert result.registered == "manual"
        assert result.skill_installed is True

    def test_claude_with_cli_registers_stdio_and_skill(self, fake_home, monkeypatch):
        monkeypatch.setattr(
            "another_brain.services.harness_connect.shutil.which", lambda _: "/usr/bin/claude"
        )
        runner = FakeRunner()
        (fake_home / ".claude").mkdir()
        result = connect(["claude-code"], home=fake_home, runner=runner)[0]
        assert result.registered == "cli"
        assert runner.calls == [
            ["claude", "mcp", "add", "-s", "user", "--transport", "stdio",
             "another-brain", "--", "another-brain"],
        ]
        assert result.skill_installed is True
        assert (fake_home / ".claude" / "skills" / "another-brain" / "SKILL.md").is_file()

    def test_claude_already_exists_removes_then_readds(self, fake_home, monkeypatch):
        monkeypatch.setattr(
            "another_brain.services.harness_connect.shutil.which", lambda _: "/usr/bin/claude"
        )
        runner = FakeRunner(stdout="MCP server another-brain already exists in user config")
        (fake_home / ".claude").mkdir()
        connect(["claude-code"], home=fake_home, runner=runner)
        assert runner.calls == [
            ["claude", "mcp", "add", "-s", "user", "--transport", "stdio",
             "another-brain", "--", "another-brain"],
            ["claude", "mcp", "remove", "another-brain", "-s", "user"],
            ["claude", "mcp", "add", "-s", "user", "--transport", "stdio",
             "another-brain", "--", "another-brain"],
        ]

    def test_codex_registers_via_cli(self, fake_home, monkeypatch):
        monkeypatch.setattr(
            "another_brain.services.harness_connect.shutil.which", lambda _: "/usr/bin/codex"
        )
        runner = FakeRunner()
        (fake_home / ".codex").mkdir()
        result = connect(["codex"], home=fake_home, runner=runner)[0]
        assert result.registered == "cli"
        assert runner.calls == [
            ["codex", "mcp", "add", "another-brain", "--", "another-brain"],
        ]
        assert result.skill_installed is True

    def test_cli_failure_raises(self, fake_home, monkeypatch):
        monkeypatch.setattr(
            "another_brain.services.harness_connect.shutil.which", lambda _: "/usr/bin/claude"
        )
        runner = FakeRunner(rc=1, stdout="boom")
        (fake_home / ".claude").mkdir()
        with pytest.raises(RuntimeError):
            connect(["claude-code"], home=fake_home, runner=runner)

    def test_json_harnesses_never_shell_out(self, fake_home):
        for name in ("cursor", "gemini-cli", "pi"):
            (fake_home / {  # detection keys on the harness dotdir
                "cursor": ".cursor",
                "gemini-cli": ".gemini",
                "pi": ".pi",
            }[name]).mkdir()
        runner = FakeRunner()
        results = connect(
            ["cursor", "gemini-cli", "pi"], home=fake_home, runner=runner
        )
        assert runner.calls == []  # JSON upsert + skill copy, zero subprocesses
        for result in results:
            assert result.registered == "json"
            assert result.skill_installed is True
        cursor_file = fake_home / ".cursor" / "mcp.json"
        assert json.loads(cursor_file.read_text())["mcpServers"]["another-brain"] == {
            "command": "another-brain"
        }
        gemini_file = fake_home / ".gemini" / "settings.json"
        assert json.loads(gemini_file.read_text())["mcpServers"]["another-brain"] == {
            "command": "another-brain"
        }
        pi_file = fake_home / ".config" / "mcp" / "mcp.json"
        assert json.loads(pi_file.read_text())["mcpServers"]["another-brain"] == {
            "command": "another-brain"
        }


# -------------------------------------------------------------------- CLI


class TestCliConnect:
    def test_bare_lists_known_and_detected(self, fake_home, capsys):
        (fake_home / ".claude").mkdir()
        assert cli.main(["connect"]) == cli.EXIT_OK
        out = capsys.readouterr().out
        assert "Known harnesses: claude-code codex cursor gemini-cli pi" in out
        assert "Detected here:   claude-code" in out

    def test_detect_prints_detected_names(self, fake_home, capsys):
        (fake_home / ".codex").mkdir()
        (fake_home / ".gemini").mkdir()
        assert cli.main(["connect", "--detect"]) == cli.EXIT_OK
        assert capsys.readouterr().out == "codex\ngemini-cli\n"

    def test_detect_none_prints_no_harnesses(self, fake_home, capsys):
        assert cli.main(["connect", "--detect"]) == cli.EXIT_OK
        assert capsys.readouterr().out == "no harnesses detected\n"

    def test_unknown_harness_exits_error(self, fake_home, capsys):
        assert cli.main(["connect", "bogus"]) == cli.EXIT_ERROR
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "unknown harness" in captured.err
        assert "bogus" in captured.err

    def test_manual_registration_is_stderr_and_error(self, fake_home, monkeypatch, capsys):
        monkeypatch.setattr(
            "another_brain.services.harness_connect.shutil.which", lambda _: None
        )
        (fake_home / ".claude").mkdir()
        assert cli.main(["connect", "claude-code"]) == cli.EXIT_ERROR
        captured = capsys.readouterr()
        assert "register" in captured.err
        assert "manual" not in captured.out  # stdout carries only real steps

    def test_json_harness_connects_clean(self, fake_home, capsys):
        (fake_home / ".cursor").mkdir()
        assert cli.main(["connect", "cursor"]) == cli.EXIT_OK
        out = capsys.readouterr().out
        assert "wrote" in out
        assert "installed the skill for cursor" in out
        assert capsys.readouterr().err == ""
