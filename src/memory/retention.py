"""RetentionPolicy — importance to Redis TTL mapping (Step 04 section 4).

Display expiry derives from EXPIRETIME at read time; there is no stored
expiry field. TTL is re-armed only by explicit brain_reinforce — never by
any read path.
"""
from __future__ import annotations

from typing import Mapping

from errors import ConfigError
from memory.models import IMPORTANCE_MAX, IMPORTANCE_MIN, validate_importance

DEFAULT_TTL_BY_IMPORTANCE: dict[int, int] = {
    5: 31_536_000,  # 365 days
    4: 15_552_000,  # 180 days
    3: 7_776_000,   # 90 days
    2: 2_592_000,   # 30 days
    1: 604_800,     # 7 days
}


class RetentionPolicy:
    def __init__(self, ttl_by_importance: Mapping[int, int] | None = None):
        table = dict(
            DEFAULT_TTL_BY_IMPORTANCE if ttl_by_importance is None else ttl_by_importance
        )
        expected = set(range(IMPORTANCE_MIN, IMPORTANCE_MAX + 1))
        if set(table) != expected:
            raise ConfigError(
                f"ttl_by_importance must define exactly importance {sorted(expected)}, "
                f"got {sorted(table)}"
            )
        for importance, ttl in table.items():
            if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl <= 0:
                raise ConfigError(
                    f"TTL for importance {importance} must be a positive int of "
                    f"seconds, got {ttl!r}"
                )
        self._table = table

    def ttl_seconds(self, importance: int) -> int:
        validate_importance(importance)
        return self._table[importance]
