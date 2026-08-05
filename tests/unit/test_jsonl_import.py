"""TASK-071: JSONL v1 envelope parsing/validation — every invalid fixture is
rejected with its violation class, the valid envelope parses, and the
remaining_ttl_ms tolerance is exactly ±1000 ms.

Builds tiny envelopes in-test with canonical lines and correct hashes so
tolerance, CRLF, and NaN rejection are exercised independently of fixtures."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from another_brain.errors import ValidationError
from another_brain.services.jsonl_import import (
    JsonlEnvelopeError,
    canonical,
    parse_envelope,
    verify_remaining_ttl,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "jsonl-v1"

EXPORTED_AT_MS = 1_785_000_000_000
EXPORT_ID = "01234567-89ab-cdef-0123-456789abcdef"


def _memory_payload(memory_id: str = "mem-1", **overrides) -> dict:
    payload = {
        "memory_id": memory_id,
        "brain_id": "brain-main",
        "agent_id": "agent-x",
        "topic": "t",
        "catalog": "note",
        "summary": "s",
        "content": "c",
        "timeline_day": "2026-07-30",
        "period_start_ms": None,
        "period_end_ms": None,
        "created_at_ms": EXPORTED_AT_MS - 10_000_000,
        "updated_at_ms": EXPORTED_AT_MS - 10_000_000,
        "importance": 3,
        "expires_at_ms": EXPORTED_AT_MS + 5_000_000,
        "deleted_at_ms": None,
        "metadata": {},
        "record_version": 1,
        "remaining_ttl_ms": 5_000_000,
    }
    payload.update(overrides)
    return payload


def _data_line(seq: int, kind: str, payload: dict, idempotency_key: str) -> str:
    return canonical(
        {
            "seq": seq,
            "kind": kind,
            "idempotency_key": idempotency_key,
            "payload": payload,
            "payload_sha256": hashlib.sha256(
                canonical(payload).encode("utf-8")
            ).hexdigest(),
        }
    )


def _write_envelope(
    tmp_path: Path,
    lines: list[str],
    *,
    memory_count: int = 1,
    audit_count: int = 0,
    raw: bool = False,
) -> Path:
    """Write manifest + lines + trailer with correct hashes/counts.

    ``raw=True`` skips canonicalization so tests can inject CRLF or NaN.
    """
    if not raw:
        lines = [canonical(json.loads(line)) for line in lines]
    manifest = {
        "kind": "manifest",
        "format": "another-brain-jsonl",
        "format_version": 1,
        "export_id": EXPORT_ID,
        "source_app_version": "0.10.3",
        "source_schema_version": "legacy-hybrid-1",
        "source_commit": "edc0e573a10bb8ea9148c9830cf19fe15f757972",
        "source_embedding_profile": "harrier-oss-v1-270m-fp32",
        "exported_at_ms": EXPORTED_AT_MS,
        "expiry_mode": "absolute_epoch_ms",
        "memory_count": memory_count,
        "audit_count": audit_count,
    }
    rolling = hashlib.sha256()
    body = [canonical(manifest)] + lines
    for line in body:
        rolling.update(line.encode("utf-8") + b"\n")
    trailer = canonical(
        {
            "kind": "trailer",
            "memory_count": memory_count,
            "audit_count": audit_count,
            "last_seq": len(lines),
            "rolling_sha256": rolling.hexdigest(),
        }
    )
    path = tmp_path / "envelope.jsonl"
    path.write_bytes(("\n".join(body + [trailer]) + "\n").encode("utf-8"))
    return path


INVALID_EXPECTED = {
    "invalid-missing-manifest-field.jsonl": "missing=",
    "invalid-bad-payload-sha256.jsonl": "payload_sha256 mismatch",
    "invalid-non-contiguous-seq.jsonl": "contiguous",
    "invalid-unsorted-memory-lines.jsonl": "not sorted",
    "invalid-non-finite-number.jsonl": "non-finite",
    "invalid-embedding-bytes-present.jsonl": "extra=",
    "invalid-bad-rolling-hash.jsonl": "rolling_sha256 mismatch",
    "invalid-crlf.jsonl": "LF-only",
}


@pytest.mark.parametrize("filename", sorted(INVALID_EXPECTED))
def test_invalid_fixture_rejected(filename: str) -> None:
    with pytest.raises(JsonlEnvelopeError) as excinfo:
        parse_envelope(FIXTURES / filename)
    assert INVALID_EXPECTED[filename] in str(excinfo.value)


def test_invalid_fixture_error_is_validation_error() -> None:
    with pytest.raises(ValidationError):
        parse_envelope(FIXTURES / "invalid-crlf.jsonl")


def test_valid_fixture_parses() -> None:
    env = parse_envelope(FIXTURES / "valid-basic.jsonl")
    assert len(env.data_lines) == 3
    kinds = [kind for _, kind, _ in env.data_lines]
    assert kinds.count("memory") == 2
    assert kinds.count("audit") == 1
    assert len(env.artifact_sha256) == 64
    assert env.export_id == env.manifest["export_id"] == EXPORT_ID
    assert env.exported_at_ms == EXPORTED_AT_MS


def _memory_line(memory_id: str = "mem-1", **overrides) -> str:
    payload = _memory_payload(memory_id, **overrides)
    return _data_line(1, "memory", payload, f"memory:brain-main:{memory_id}")


@pytest.mark.parametrize("delta_ms", [1000, -1000])
def test_remaining_ttl_tolerance_accepts_1000ms(
    tmp_path: Path, delta_ms: int
) -> None:
    payload = _memory_payload("mem-1")
    payload["remaining_ttl_ms"] = payload["remaining_ttl_ms"] + delta_ms
    path = _write_envelope(tmp_path, [_memory_line(**payload)])
    env = parse_envelope(path)
    assert env.data_lines[0][2]["remaining_ttl_ms"] == 5_000_000 + delta_ms


@pytest.mark.parametrize("delta_ms", [1001, -1001])
def test_remaining_ttl_tolerance_rejects_1001ms(
    tmp_path: Path, delta_ms: int
) -> None:
    payload = _memory_payload("mem-1")
    payload["remaining_ttl_ms"] = payload["remaining_ttl_ms"] + delta_ms
    path = _write_envelope(tmp_path, [_memory_line(**payload)])
    with pytest.raises(JsonlEnvelopeError, match="1000 ms"):
        parse_envelope(path)


def test_verify_remaining_ttl_boundary() -> None:
    payload = _memory_payload("mem-1")
    verify_remaining_ttl(payload, EXPORTED_AT_MS)
    payload["remaining_ttl_ms"] = 5_001_000
    verify_remaining_ttl(payload, EXPORTED_AT_MS)
    payload["remaining_ttl_ms"] = 5_001_001
    with pytest.raises(JsonlEnvelopeError):
        verify_remaining_ttl(payload, EXPORTED_AT_MS)


def test_crlf_rejected_even_with_matching_hashes(tmp_path: Path) -> None:
    payload = _memory_payload("mem-1")
    line = _data_line(1, "memory", payload, "memory:brain-main:mem-1")
    path = _write_envelope(tmp_path, [line], raw=True)
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    with pytest.raises(JsonlEnvelopeError, match="LF-only"):
        parse_envelope(path)


def test_nan_literal_rejected(tmp_path: Path) -> None:
    payload = _memory_payload("mem-1")
    line = _data_line(1, "memory", payload, "memory:brain-main:mem-1")
    path = _write_envelope(tmp_path, [line], raw=True)
    text = path.read_text("utf-8")
    text = text.replace("\"importance\":3", "\"importance\":NaN")
    path.write_text(text, "utf-8")
    with pytest.raises(JsonlEnvelopeError, match="non-finite"):
        parse_envelope(path)
