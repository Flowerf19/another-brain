"""AuditEvent — a secret-free record of one memory mutation.

Audit exists for lifecycle traceability: who created / renewed / deleted a
memory, and when. It deliberately stores NO memory text (summary, content,
topic, metadata may hold sensitive data) — only structural facts. Events live
in per-brain-per-day HASHes (Step 04 §2.2) with a rolling retention TTL.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from errors import ValidationError


class AuditAction:
    """The mutations that leave a trail (Step 04 §4.2). Reads never audit."""

    REMEMBER = "remember"
    REINFORCE = "reinforce"
    FORGET = "forget"
    RESTORE = "restore"
    HARD_DELETE = "hard_delete"


# Keys that must never appear in an audit detail payload — they carry the
# memory's actual text and would defeat the secret-free guarantee.
_FORBIDDEN_DETAIL_KEYS = frozenset({"summary", "content", "topic", "metadata"})


@dataclass(frozen=True)
class AuditEvent:
    action: str
    memory_id: str
    brain_id: str
    agent_id: str  # the actor who performed the mutation, not the memory author
    ts: float
    detail: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self) -> None:
        leaked = _FORBIDDEN_DETAIL_KEYS & set(self.detail)
        if leaked:
            raise ValidationError(
                f"audit detail must stay secret-free — drop {sorted(leaked)}"
            )

    def to_json(self) -> str:
        # allow_nan=False mirrors the strict-JSON rule in MemoryService.remember:
        # NaN/Infinity pass the default dumps but are not valid JSON.
        return json.dumps(
            {
                "event_id": self.event_id,
                "action": self.action,
                "memory_id": self.memory_id,
                "brain_id": self.brain_id,
                "agent_id": self.agent_id,
                "ts": self.ts,
                "detail": self.detail,
            },
            ensure_ascii=False,
            allow_nan=False,
        )

    @classmethod
    def from_json(cls, raw: str | bytes) -> "AuditEvent":
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode()
        data = json.loads(raw)
        detail = data.get("detail") or {}
        if not isinstance(detail, dict):
            detail = {}
        return cls(
            action=data["action"],
            memory_id=data["memory_id"],
            brain_id=data["brain_id"],
            agent_id=data["agent_id"],
            ts=float(data["ts"]),
            detail=detail,
            event_id=data["event_id"],
        )
