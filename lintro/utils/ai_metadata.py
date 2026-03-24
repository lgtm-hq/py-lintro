"""Shared AI metadata accessors for tool results.

Provides a single implementation for extracting integer counts from
``ai_metadata`` dictionaries attached to :class:`ToolResult` objects.
"""

from __future__ import annotations


def get_ai_count(result: object, key: str) -> int:
    """Get an integer AI metadata count from a result object.

    Falls back from ``applied_count`` to ``fixed_count`` for
    backward compatibility with older metadata.

    Args:
        result: Tool result with an optional ``ai_metadata`` dict attribute.
        key: Metadata key to read (e.g. ``"applied_count"``).

    Returns:
        Non-negative integer count, or ``0`` when absent/invalid.
    """
    ai_metadata = getattr(result, "ai_metadata", None)
    if not isinstance(ai_metadata, dict):
        return 0
    value = ai_metadata.get(key)
    if value is None and key == "applied_count":
        value = ai_metadata.get("fixed_count", 0)
    if value is None:
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
