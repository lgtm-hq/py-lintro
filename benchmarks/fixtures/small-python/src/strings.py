"""String helpers used as part of the small-python benchmark fixture."""

from __future__ import annotations

from collections.abc import Iterable


def slugify(value: str) -> str:
    """Convert a string into a lowercase, hyphen-separated slug.

    Args:
        value: Input text to slugify.

    Returns:
        str: The slugified text.
    """
    parts = [chunk for chunk in value.strip().lower().split() if chunk]
    return "-".join(parts)


def truncate(value: str, *, limit: int, suffix: str = "...") -> str:
    """Truncate a string to a maximum length, appending a suffix.

    Args:
        value: Input text.
        limit: Maximum length of the returned string including the suffix.
        suffix: Suffix appended when truncation occurs.

    Returns:
        str: The possibly-truncated text.
    """
    if len(value) <= limit:
        return value
    keep = max(limit - len(suffix), 0)
    return value[:keep] + suffix


def join_nonempty(values: Iterable[str], *, separator: str = ", ") -> str:
    """Join non-empty strings with a separator.

    Args:
        values: Candidate strings.
        separator: Separator inserted between non-empty values.

    Returns:
        str: The joined string.
    """
    return separator.join(value for value in values if value)
