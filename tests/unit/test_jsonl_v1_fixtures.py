"""TASK-033: JSONL v1 contract fixtures — the reference validator accepts the
valid envelope and rejects every invalid fixture with its specific reason."""
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "jsonl-v1"
VALIDATOR = Path(__file__).resolve().parents[2] / "scripts" / "validate_jsonl_v1.py"

INVALID_EXPECTED = {
    "invalid-missing-manifest-field.jsonl": "missing=['expiry_mode']",
    "invalid-bad-payload-sha256.jsonl": "payload_sha256 mismatch",
    "invalid-non-contiguous-seq.jsonl": "seq must be contiguous",
    "invalid-unsorted-memory-lines.jsonl": "not sorted by (brain_id, memory_id)",
    "invalid-non-finite-number.jsonl": "non-finite number",
    "invalid-embedding-bytes-present.jsonl": "extra=['embedding']",
    "invalid-bad-rolling-hash.jsonl": "rolling_sha256 mismatch",
    "invalid-crlf.jsonl": "LF-only",
}


def run_validator(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), str(path)], capture_output=True, text=True
    )


def test_valid_fixture_passes():
    result = run_validator(FIXTURES / "valid-basic.jsonl")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("OK")


@pytest.mark.parametrize("filename", sorted(INVALID_EXPECTED))
def test_invalid_fixture_rejected(filename):
    result = run_validator(FIXTURES / filename)
    assert result.returncode == 1
    assert INVALID_EXPECTED[filename] in result.stderr


def test_fixture_set_matches_contract_doc():
    """Every invalid-* fixture is covered above; no stale/missing cases."""
    on_disk = {p.name for p in FIXTURES.glob("invalid-*.jsonl")}
    assert on_disk == set(INVALID_EXPECTED)
