"""Time helpers. All timestamps in API responses come from here."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo


def now(tz_name: str = "UTC") -> tuple[str, str]:
    """Current moment in ``tz_name`` as (``YYYY-MM-DD``, ``HH:MM:SS``)."""
    moment = datetime.now(ZoneInfo(tz_name))
    return moment.strftime("%Y-%m-%d"), moment.strftime("%H:%M:%S")


def utc_iso_now() -> str:
    """ISO-8601 UTC timestamp with millisecond precision and a ``Z`` suffix (AT Protocol ``createdAt``)."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
