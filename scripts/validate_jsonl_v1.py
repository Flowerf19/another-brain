#!/usr/bin/env python3
"""Reference validator for the `another-brain-jsonl` v1 envelope.

Contract: .agents/contracts/another-brain-jsonl-v1.md (frozen, TASK-033).
Stdlib only; this is a fixture/evidence tool, not the GOAL-014 importer.

Usage: validate_jsonl_v1.py <file.jsonl> [...]
Exit 0 when every file is valid, 1 on the first violation per file.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import uuid
from pathlib import Path

TIMELINE_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SCOPES = {"user", "project", "global"}
AUDIT_ACTIONS = {"remember", "reinforce", "forget", "restore", "hard_delete"}
FORBIDDEN_AUDIT_KEYS = {"topic", "summary", "content", "metadata"}

MANIFEST_KEYS = {
    "kind", "format", "format_version", "export_id", "source_app_version",
    "source_schema_version", "source_commit", "source_embedding_profile",
    "exported_at_ms", "expiry_mode", "memory_count", "audit_count",
}
DATA_KEYS = {"seq", "kind", "idempotency_key", "payload", "payload_sha256"}
MEMORY_PAYLOAD_KEYS = {
    "memory_id", "brain_id", "agent_id", "scope", "scope_id", "topic",
    "catalog", "summary", "content", "timeline_day", "period_start_ms",
    "period_end_ms", "created_at_ms", "updated_at_ms", "importance",
    "expires_at_ms", "deleted_at_ms", "metadata", "record_version",
    "remaining_ttl_ms",
}
AUDIT_PAYLOAD_KEYS = {
    "event_id", "brain_id", "memory_id", "agent_id", "action", "event_at_ms",
    "detail",
}
TRAILER_KEYS = {"kind", "memory_count", "audit_count", "last_seq", "rolling_sha256"}


class Violation(Exception):
    pass


def canonical(obj: object) -> str:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite number literal {value!r}")


def _parse_line(raw: str, line_no: int) -> dict:
    try:
        obj = json.loads(raw, parse_constant=_reject_constant)
    except ValueError as exc:
        raise Violation(f"line {line_no}: invalid JSON ({exc})") from None
    if not isinstance(obj, dict):
        raise Violation(f"line {line_no}: expected a JSON object")
    if canonical(obj) != raw:
        raise Violation(
            f"line {line_no}: line is not canonical JSON"
            " (sorted keys, compact separators, ensure_ascii=false)"
        )
    return obj


def _check_int(value: object, name: str, line: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise Violation(f"{line}: {name} must be an integer, got {value!r}")


def _check_memory_payload(p: dict, exported_at_ms: int, line: str) -> None:
    if set(p) != MEMORY_PAYLOAD_KEYS:
        raise Violation(
            f"{line}: memory payload keys differ from the contract"
            f" (extra={sorted(set(p) - MEMORY_PAYLOAD_KEYS)},"
            f" missing={sorted(MEMORY_PAYLOAD_KEYS - set(p))})"
        )
    if p["scope"] not in SCOPES:
        raise Violation(f"{line}: invalid scope {p['scope']!r}")
    if not isinstance(p["scope_id"], str) or not p["scope_id"]:
        raise Violation(f"{line}: scope_id must be a non-empty string")
    if p["scope"] == "global" and p["scope_id"] != "global":
        raise Violation(f"{line}: scope=global pins scope_id='global'")
    if not TIMELINE_DAY_RE.match(str(p["timeline_day"])):
        raise Violation(f"{line}: timeline_day must be YYYY-MM-DD")
    for key in ("created_at_ms", "updated_at_ms", "expires_at_ms", "remaining_ttl_ms"):
        _check_int(p[key], key, line)
    for key in ("period_start_ms", "period_end_ms", "deleted_at_ms"):
        _check_int(p[key], key, line, nullable=True)
    if p["period_start_ms"] is not None and p["period_end_ms"] is not None \
            and p["period_end_ms"] < p["period_start_ms"]:
        raise Violation(f"{line}: period_end_ms < period_start_ms")
    if isinstance(p["importance"], bool) or not 1 <= p["importance"] <= 5:
        raise Violation(f"{line}: importance must be 1..5")
    if not isinstance(p["metadata"], dict):
        raise Violation(f"{line}: metadata must be an object")
    if p["remaining_ttl_ms"] != max(0, p["expires_at_ms"] - exported_at_ms):
        raise Violation(
            f"{line}: remaining_ttl_ms != max(0, expires_at_ms - exported_at_ms)"
        )


def _check_audit_payload(p: dict, line: str) -> None:
    if set(p) != AUDIT_PAYLOAD_KEYS:
        raise Violation(
            f"{line}: audit payload keys differ from the contract"
            f" (extra={sorted(set(p) - AUDIT_PAYLOAD_KEYS)},"
            f" missing={sorted(AUDIT_PAYLOAD_KEYS - set(p))})"
        )
    if FORBIDDEN_AUDIT_KEYS & set(p):
        raise Violation(f"{line}: audit payload carries forbidden memory-text keys")
    if p["action"] not in AUDIT_ACTIONS:
        raise Violation(f"{line}: invalid audit action {p['action']!r}")
    _check_int(p["event_at_ms"], "event_at_ms", line)
    if not isinstance(p["detail"], dict):
        raise Violation(f"{line}: detail must be an object")


def validate_bytes(data: bytes) -> None:
    if b"\r" in data:
        raise Violation("CR line ending found; envelope is LF-only")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Violation(f"not valid UTF-8: {exc}") from None
    if not text.endswith("\n"):
        raise Violation("file must end with a trailing LF")
    lines = text[:-1].split("\n")
    if len(lines) < 2:
        raise Violation("envelope needs at least a manifest and a trailer")

    rolling = hashlib.sha256()
    manifest = _parse_line(lines[0], 1)
    rolling.update(lines[0].encode("utf-8") + b"\n")
    if set(manifest) != MANIFEST_KEYS:
        raise Violation(
            "line 1: manifest keys differ from the contract"
            f" (extra={sorted(set(manifest) - MANIFEST_KEYS)},"
            f" missing={sorted(MANIFEST_KEYS - set(manifest))})"
        )
    if manifest["kind"] != "manifest":
        raise Violation("line 1: kind must be 'manifest'")
    if manifest["format"] != "another-brain-jsonl":
        raise Violation("line 1: format must be 'another-brain-jsonl'")
    if manifest["format_version"] != 1:
        raise Violation("line 1: format_version must be 1")
    if manifest["expiry_mode"] != "absolute_epoch_ms":
        raise Violation("line 1: expiry_mode must be 'absolute_epoch_ms'")
    try:
        uuid.UUID(str(manifest["export_id"]))
    except ValueError:
        raise Violation("line 1: export_id must be a UUID") from None
    _check_int(manifest["exported_at_ms"], "exported_at_ms", "line 1")
    for key in ("memory_count", "audit_count"):
        _check_int(manifest[key], key, "line 1")
        if manifest[key] < 0:
            raise Violation(f"line 1: {key} must be non-negative")

    data_lines = lines[1:-1]
    trailer = _parse_line(lines[-1], len(lines))
    if len(data_lines) != manifest["memory_count"] + manifest["audit_count"]:
        raise Violation(
            f"data line count {len(data_lines)} != memory_count + audit_count"
            f" ({manifest['memory_count']} + {manifest['audit_count']})"
        )

    memories: list[tuple[str, str]] = []
    audits: list[tuple[str, int, str]] = []
    seen_audit = False
    for offset, raw in enumerate(data_lines):
        line_no = offset + 2
        where = f"line {line_no}"
        obj = _parse_line(raw, line_no)
        rolling.update(raw.encode("utf-8") + b"\n")
        if set(obj) != DATA_KEYS:
            raise Violation(f"{where}: data line keys differ from the contract")
        if obj["seq"] != offset + 1:
            raise Violation(f"{where}: seq must be contiguous from 1 (got {obj['seq']})")
        payload = obj["payload"]
        if not isinstance(payload, dict):
            raise Violation(f"{where}: payload must be an object")
        digest = hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()
        if obj["payload_sha256"] != digest:
            raise Violation(f"{where}: payload_sha256 mismatch")
        kind = obj["kind"]
        if kind == "memory":
            if seen_audit:
                raise Violation(f"{where}: memory line after audit lines")
            _check_memory_payload(payload, manifest["exported_at_ms"], where)
            key = (payload["brain_id"], payload["memory_id"])
            if obj["idempotency_key"] != f"memory:{key[0]}:{key[1]}":
                raise Violation(f"{where}: bad memory idempotency_key")
            memories.append(key)
        elif kind == "audit":
            seen_audit = True
            _check_audit_payload(payload, where)
            key = (payload["brain_id"], payload["event_at_ms"], payload["event_id"])
            if obj["idempotency_key"] != f"audit:{key[0]}:{key[2]}":
                raise Violation(f"{where}: bad audit idempotency_key")
            audits.append(key)
        else:
            raise Violation(f"{where}: kind must be 'memory' or 'audit'")

    if memories != sorted(memories):
        raise Violation("memory lines are not sorted by (brain_id, memory_id)")
    if audits != sorted(audits):
        raise Violation("audit lines are not sorted by (brain_id, event_at_ms, event_id)")

    if set(trailer) != TRAILER_KEYS:
        raise Violation("trailer: keys differ from the contract")
    if trailer["kind"] != "trailer":
        raise Violation("trailer: kind must be 'trailer'")
    if trailer["memory_count"] != manifest["memory_count"] \
            or trailer["audit_count"] != manifest["audit_count"]:
        raise Violation("trailer: counts differ from manifest")
    if trailer["last_seq"] != len(data_lines):
        raise Violation("trailer: last_seq must equal the number of data lines")
    if trailer["rolling_sha256"] != rolling.hexdigest():
        raise Violation("trailer: rolling_sha256 mismatch")


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    status = 0
    for name in argv:
        try:
            validate_bytes(Path(name).read_bytes())
        except Violation as exc:
            print(f"{name}: INVALID: {exc}", file=sys.stderr)
            status = 1
        else:
            print(f"{name}: OK")
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
