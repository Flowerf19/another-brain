"""TASK-055: accepted concurrency workload against the real repository
(quick mode). The full locked parameters (5 seeds, 500 ops/worker, both
vector modes) run as evidence via ``run_repository.py`` without ``--quick``."""
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "concurrency" / "run_repository.py"


@pytest.mark.slow
def test_concurrency_workload_real_repository():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--quick"], capture_output=True, text=True, timeout=900
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "REPOSITORY WORKLOAD PASS" in result.stdout
