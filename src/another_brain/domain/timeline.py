"""``timeline_day`` derivation — the one place epoch ms becomes a diary day.

``timeline_day`` is ``YYYY-MM-DD`` in the configured ``TIMELINE_TIMEZONE``
(:mod:`another_brain.config`), computed by the service at write time and then
persisted; readers never recompute it from a row's ``created_at``. Keeping the
conversion here means the memory write path and the audit write path cannot
disagree about which day a mutation belongs to.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

DAY_FORMAT = "%Y-%m-%d"


def timeline_day_for(epoch_ms: int, tz_name: str) -> str:
    """The ``YYYY-MM-DD`` diary day of ``epoch_ms`` in ``tz_name``.

    ``epoch_ms`` is signed (pre-1970 values are legal per contract), so the
    conversion goes through an aware UTC datetime rather than
    ``fromtimestamp`` local-time handling.
    """
    moment = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
    return moment.astimezone(ZoneInfo(tz_name)).strftime(DAY_FORMAT)
