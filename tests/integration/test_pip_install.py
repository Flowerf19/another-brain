"""TASK-006: pip-install gate as a slow integration test.

Installs the local checkout with standard pip (PEP 517 hatchling build) into
a throwaway venv created by `python -m venv`, and asserts the console script
works while imports resolve from the venv, never the checkout. Standard pip +
venv only — the gate does not call uv."""
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "installer" / "linux" / "check-pip-install.sh"


@pytest.mark.slow
def test_pip_install_gate():
    result = subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, timeout=600
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "PASS: pip install gate" in result.stdout
