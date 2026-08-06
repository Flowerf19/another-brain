"""TASK-041: clean-wheel install gate as a slow integration test.

Builds sdist/wheel with `uv build --no-sources`, installs the wheel into a
throwaway venv, and asserts the console script works while imports resolve
from the installed wheel, never the checkout."""
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "installer" / "linux" / "check-wheel-install.sh"


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is required to build/install")
def test_clean_wheel_install_gate():
    result = subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, timeout=600
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "PASS: clean wheel install gate" in result.stdout
