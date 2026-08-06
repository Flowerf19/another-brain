"""TASK-007: concurrency harness validation against the toy adapter (quick
mode). The full locked parameters run in TASK-055 against the real repository."""
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "concurrency" / "run_harness.py"


@pytest.mark.slow
def test_concurrency_harness_toy_validation():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--quick"], capture_output=True, text=True, timeout=600
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "HARNESS PASS" in result.stdout
