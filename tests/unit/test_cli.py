"""TASK-040: CLI parsing, exit codes, stderr discipline, and HTTP bind
precedence. Commands whose subsystems are still unimplemented must exit
EXIT_UNAVAILABLE with a typed message on stderr (stdout stays clean for
MCP frames)."""
import pytest

from another_brain import cli


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    monkeypatch.setenv("BRAIN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("BRAIN_MODEL_CACHE_DIR", str(tmp_path / "models"))
    for key in ("MCP_HTTP_HOST", "MCP_HTTP_PORT", "BRAIN_ID", "TIMELINE_TIMEZONE"):
        monkeypatch.delenv(key, raising=False)


class TestParsingAndExitCodes:
    def test_doctor_is_live_and_reports_health(self, capsys):
        """TASK-084: doctor runs in an empty profile — model/database warn,
        nothing fails, exit EXIT_OK, human-readable stdout."""
        assert cli.main(["doctor"]) == cli.EXIT_OK
        out = capsys.readouterr().out
        assert "[warn] model" in out.replace("  ", " ")
        assert "no database yet" in out
        assert "summary:" in out

    def test_recent_empty_store_prints_no_memories(self, capsys):
        """TASK-074: recent is live against a fresh store; no model needed."""
        assert cli.main(["recent"]) == cli.EXIT_OK
        out = capsys.readouterr().out
        assert "no memories" in out

    def test_recent_rejects_out_of_range_limit(self, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.main(["recent", "--limit", "0"])
        assert exc.value.code == 2
        assert "between 1 and 100" in capsys.readouterr().err

    def test_admin_restore_unknown_id_is_typed_error(self, capsys):
        """TASK-074: not_found is honest — stderr message, EXIT_ERROR."""
        assert cli.main(["admin", "restore", "unknown-id"]) == cli.EXIT_ERROR
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "not_found" in captured.err

    def test_admin_hard_delete_unknown_id_is_typed_error(self, capsys):
        assert cli.main(["admin", "hard-delete", "unknown-id"]) == cli.EXIT_ERROR
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "not_found" in captured.err

    def test_import_jsonl_missing_file_is_typed_error(self, capsys):
        """TASK-071: the command landed; a missing artifact is a storage error."""
        assert cli.main(["import-jsonl", "/nonexistent/export.jsonl"]) == cli.EXIT_ERROR
        captured = capsys.readouterr()
        assert captured.out == ""  # errors go to stderr only
        assert "envelope" in captured.err

    def test_version(self, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.main(["--version"])
        assert exc.value.code == 0
        assert cli.VERSION in capsys.readouterr().out

    def test_model_status_reports_not_installed_without_loading(self, capsys):
        """Empty cache: status answers from disk state, exit 0, no model load."""
        assert cli.main(["model", "status"]) == cli.EXIT_OK
        out = capsys.readouterr().out
        assert "installed: no" in out
        assert "profile: q4" in out
        assert "revision:" in out

    def test_model_status_installed_yes_lists_all_files(self, monkeypatch, capsys):
        """TASK-046: status answers from files on disk — never loads the model."""
        from another_brain.services.embedding import model_installer

        ok = {name: "ok" for name, _ in model_installer.MODEL_MANIFEST.files}
        monkeypatch.setattr(model_installer, "verify", lambda cache_dir, manifest=None: ok)
        assert cli.main(["model", "status"]) == cli.EXIT_OK
        out = capsys.readouterr().out
        assert "installed: yes" in out
        assert "config.json: ok" in out
        assert "onnx/model_q4.onnx: ok" in out
        assert "tokenizer.json: ok" in out
        assert "onnx/model_q4.onnx_data: ok" in out
        assert "tokenizer_config.json: ok" in out

    def test_model_pull_failure_is_typed_error(self, monkeypatch, capsys):
        from another_brain.services.embedding import model_installer
        from another_brain.errors import ModelDownloadError

        def _failing_install(cache_dir, **kwargs):
            raise ModelDownloadError("cannot reach https://…")

        monkeypatch.setattr(model_installer, "install", _failing_install)
        assert cli.main(["model", "pull"]) == cli.EXIT_ERROR
        captured = capsys.readouterr()
        assert captured.out == ""  # errors go to stderr only
        assert "model pull failed" in captured.err

    def test_model_status_answers_without_loading(self, capsys):
        """Status works from disk state; heavy imports are covered by the
        subprocess test below (in-process sys.modules is polluted by other
        test modules)."""
        assert cli.main(["model", "status"]) == cli.EXIT_OK
        assert "installed: no" in capsys.readouterr().out

    def test_help_lists_all_commands(self, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.main(["--help"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        for cmd in ("serve", "model", "doctor", "recent", "admin", "import-jsonl"):
            assert cmd in out


class TestHttpBindPrecedence:
    def test_cli_flag_wins_over_env(self, monkeypatch, capsys):
        monkeypatch.setenv("MCP_HTTP_HOST", "127.0.0.2")
        monkeypatch.setenv("MCP_HTTP_PORT", "2905")
        assert cli.main(["serve", "--http", "--host", "127.0.0.3", "--port", "3905"]) == 3
        assert "127.0.0.3:3905" in capsys.readouterr().err

    def test_env_wins_over_default(self, monkeypatch, capsys):
        monkeypatch.setenv("MCP_HTTP_HOST", "127.0.0.2")
        monkeypatch.setenv("MCP_HTTP_PORT", "2905")
        assert cli.main(["serve", "--http"]) == 3
        assert "127.0.0.2:2905" in capsys.readouterr().err

    def test_default_bind(self, capsys):
        assert cli.main(["serve", "--http"]) == 3
        assert "127.0.0.1:1905" in capsys.readouterr().err

    @pytest.mark.parametrize(
        "host", ["localhost", "0.0.0.0", "::", "192.168.1.5", "169.254.0.1", "fe80::1"]
    )
    def test_flag_rejects_non_loopback(self, host, capsys):
        assert cli.main(["serve", "--http", "--host", host]) == cli.EXIT_CONFIG
        assert "loopback" in capsys.readouterr().err

    @pytest.mark.parametrize("port", ["0", "65536", "abc"])
    def test_flag_rejects_invalid_port(self, port):
        assert cli.main(["serve", "--http", "--port", port]) == cli.EXIT_CONFIG

    def test_serve_without_http_is_stdio(self, capsys):
        """No bind validation or HTTP message when --http is absent."""
        assert cli.main(["serve"]) == cli.EXIT_UNAVAILABLE
        err = capsys.readouterr().err
        assert "HTTP" not in err


class TestStartupImports:
    def test_cli_import_has_no_legacy_or_heavy_deps(self):
        """`another-brain` startup never imports Redis/Torch/ST/ONNX (TASK-040)."""
        import subprocess
        import sys
        from pathlib import Path

        src = Path(__file__).resolve().parents[2] / "src"
        code = (
            "import sys; sys.path.insert(0, %r);"
            "import another_brain.cli;"
            "bad = [m for m in ('redis', 'torch', 'sentence_transformers',"
            " 'onnxruntime', 'tokenizers') if m in sys.modules];"
            "assert not bad, bad"
        ) % str(src)
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
