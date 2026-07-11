"""RedisKeyBuilder — key formats from Step 04 section 2.

The type segment comes before brain_id (ab:memory:{brain_id}:{memory_id})
so each key family has a fixed literal prefix: the search index PREFIX
(ab:memory:) can never match audit or meta keys.
"""
from __future__ import annotations

import re

from errors import ValidationError

DEFAULT_KEY_PREFIX = "ab"

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _segment(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{field_name} must be a non-empty string, got {value!r}")
    if ":" in value:
        raise ValidationError(f"{field_name} must not contain ':', got {value!r}")
    return value


class RedisKeyBuilder:
    def __init__(self, prefix: str = DEFAULT_KEY_PREFIX):
        self._prefix = _segment(prefix, "key prefix")

    @property
    def memory_prefix(self) -> str:
        """Literal index PREFIX for FT.CREATE."""
        return f"{self._prefix}:memory:"

    @property
    def index_name(self) -> str:
        return f"{self._prefix}:idx:memory"

    @property
    def meta_key(self) -> str:
        return f"{self._prefix}:idx:meta"

    def memory_key(self, brain_id: str, memory_id: str) -> str:
        return (
            f"{self.memory_prefix}"
            f"{_segment(brain_id, 'brain_id')}:{_segment(memory_id, 'memory_id')}"
        )

    def audit_key(self, brain_id: str, day: str) -> str:
        if not isinstance(day, str) or not _DAY_RE.match(day):
            raise ValidationError(f"audit day must be YYYY-MM-DD, got {day!r}")
        return f"{self._prefix}:audit:{_segment(brain_id, 'brain_id')}:{day}"

    def parse_memory_key(self, key: str) -> tuple[str, str]:
        """Split a memory key back into (brain_id, memory_id) — memory_id is
        not stored as a hash field, it is derived from the key on read."""
        prefix = self.memory_prefix
        if not isinstance(key, str) or not key.startswith(prefix):
            raise ValidationError(f"not a memory key for prefix {prefix!r}: {key!r}")
        brain_id, sep, memory_id = key[len(prefix):].partition(":")
        if not sep or not brain_id or not memory_id or ":" in memory_id:
            raise ValidationError(f"malformed memory key: {key!r}")
        return brain_id, memory_id
