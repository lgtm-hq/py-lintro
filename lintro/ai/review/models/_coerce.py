"""Shared coercion helpers for parsing untrusted review-state payloads.

The review-state blob lives in a PR comment, so every decoded value is
untrusted: it may be missing, of the wrong type, or hand-edited. These helpers
give the record dataclasses one consistent, non-raising coercion path.
"""

from __future__ import annotations

from typing import Any

__all__ = ["coerce_float", "coerce_int"]


def coerce_int(value: Any, *, default: int = 0) -> int:
    """Coerce an untrusted JSON value to an int.

    Args:
        value: Raw value decoded from the state blob.
        default: Value used when coercion is impossible.

    Returns:
        The coerced integer, or ``default``.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def coerce_float(value: Any, *, default: float = 0.0) -> float:
    """Coerce an untrusted JSON value to a float.

    Args:
        value: Raw value decoded from the state blob.
        default: Value used when coercion is impossible.

    Returns:
        The coerced float, or ``default``.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
