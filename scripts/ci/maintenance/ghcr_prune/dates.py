"""ISO-8601 datetime parsing and age comparison helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from loguru import logger


def parse_iso_datetime(iso_str: str) -> datetime | None:
    """Parse ISO 8601 datetime string to a timezone-aware UTC datetime.

    Args:
        iso_str: ISO format datetime string (e.g. ``"2026-01-31T20:05:01Z"``).

    Returns:
        Timezone-aware datetime in UTC, or ``None`` if parsing fails.
    """
    if not iso_str:
        return None
    try:
        # Handle Z suffix and +00:00 formats
        normalized = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        logger.warning("Failed to parse datetime: {}", iso_str)
        return None


def is_older_than_days(created_at: str, min_age_days: int) -> bool:
    """Check whether a version is older than ``min_age_days``.

    Conservative on parse failure (returns ``False`` so the version is not
    deleted).

    Args:
        created_at: ISO timestamp when version was created.
        min_age_days: Minimum age in days before deletion is allowed.

    Returns:
        True if version is older than ``min_age_days``, False otherwise.
    """
    created = parse_iso_datetime(iso_str=created_at)
    if created is None:
        return False
    cutoff = datetime.now(UTC) - timedelta(days=min_age_days)
    return created < cutoff
