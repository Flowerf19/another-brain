"""CLI ``setup`` — one-shot onboarding composing model pull + connect.

Both steps are idempotent and individually covered elsewhere; these tests
pin the composition: order (model first, connect only if it succeeds),
the no-harness path, and re-run safety.
"""

from pathlib import Path

import pytest

from another_brain import cli
from another_brain.services.embedding import model_installer


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    """Isolated profile dirs + fake HOME so connect touches nothing real."""
    monkeypatch.setenv("BRAIN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("BRAIN_MODEL_CACHE_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("HOME", str(tmp_path))
    # Path.home() honors $HOME only on POSIX; patch for cross-OS determinism.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


@pytest.fixture
def ok_install(monkeypatch):
    """Model install that succeeds without any download."""
    monkeypatch.setattr(
        model_installer, "install", lambda cache_dir, **kwargs: cache_dir / "q4"
    )


class TestSetup:
    def test_pulls_model_and_connects_every_detected_harness(
        self, fake_env, ok_install, capsys
    ):
        (fake_env / ".cursor").mkdir()
        (fake_env / ".pi").mkdir()

        assert cli.main(["setup"]) == cli.EXIT_OK
        out = capsys.readouterr().out
        assert "model installed" in out
        assert "detected harnesses" in out
        assert "cursor" in out and "pi" in out
        assert "setup complete" in out

        # real connect side effects landed in the fake home
        assert (fake_env / ".cursor" / "mcp.json").is_file()
        assert (fake_env / ".config" / "mcp" / "mcp.json").is_file()
        assert (fake_env / ".cursor" / "skills" / "another-brain" / "SKILL.md").is_file()

    def test_no_harnesses_detected_is_ok_with_guidance(
        self, fake_env, ok_install, capsys
    ):
        assert cli.main(["setup"]) == cli.EXIT_OK
        out = capsys.readouterr().out
        assert "model installed" in out
        assert "no harnesses detected" in out
        assert "connect [" in out

    def test_model_pull_failure_aborts_before_connect(
        self, fake_env, monkeypatch, capsys
    ):
        from another_brain.errors import ModelDownloadError

        def _failing(cache_dir, **kwargs):
            raise ModelDownloadError("cannot reach https://…")

        monkeypatch.setattr(model_installer, "install", _failing)
        (fake_env / ".cursor").mkdir()

        assert cli.main(["setup"]) == cli.EXIT_ERROR
        captured = capsys.readouterr()
        assert captured.out == ""  # errors go to stderr only
        assert "model pull failed" in captured.err
        assert not (fake_env / ".cursor" / "mcp.json").exists()  # connect not attempted

    def test_rerun_is_safe_and_stays_ok(self, fake_env, ok_install, capsys):
        (fake_env / ".cursor").mkdir()
        assert cli.main(["setup"]) == cli.EXIT_OK
        assert cli.main(["setup"]) == cli.EXIT_OK
        out = capsys.readouterr().out
        assert out.count("setup complete") == 2
