"""Core helpers for the tool metadata attached to results.

:attr:`lintro.models.core.tool_result.ToolResult.metadata` carries a plain
JSON-serializable dict. Most of its keys are produced by the AI layer, but the
container itself is not AI-specific — osv-scanner stores its suppression
classifications there with AI fully disabled — so the accessors and the
whitelist normalizer live in core and never import :mod:`lintro.ai`
(issue #724).
"""

from __future__ import annotations

import copy
from typing import Any


def get_ai_count(result: object, key: str) -> int:
    """Get an integer AI metadata count from a result object.

    Falls back from ``applied_count`` to ``fixed_count`` for
    backward compatibility with older metadata.

    Args:
        result: Tool result with an optional ``metadata`` dict attribute.
        key: Metadata key to read (e.g. ``"applied_count"``).

    Returns:
        Non-negative integer count, or ``0`` when absent/invalid.
    """
    metadata = getattr(result, "metadata", None)
    if not isinstance(metadata, dict):
        return 0
    value = metadata.get(key)
    if value is None and key == "applied_count":
        value = metadata.get("fixed_count", 0)
    if value is None:
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def normalize_tool_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy and current tool metadata into one stable shape.

    A pure ``dict`` -> ``dict`` whitelist: it constructs no AI objects and
    imports nothing from :mod:`lintro.ai`, which is what keeps the JSON
    output path free of AI imports.

    Args:
        raw: Raw metadata dict as attached to a tool result.

    Returns:
        A new dict containing only recognized keys, in their current shape.
    """
    normalized: dict[str, Any] = {}

    summary = raw.get("summary")
    if isinstance(summary, dict):
        normalized["summary"] = summary

    fix_suggestions = raw.get("fix_suggestions")
    if fix_suggestions is None:
        fix_suggestions = raw.get("suggestions")
    if isinstance(fix_suggestions, list):
        normalized["fix_suggestions"] = [
            item for item in fix_suggestions if isinstance(item, dict)
        ]

    fixed_count = raw.get("fixed_count")
    if isinstance(fixed_count, int):
        normalized["fixed_count"] = fixed_count

    applied_count = raw.get("applied_count")
    if isinstance(applied_count, int):
        normalized["applied_count"] = applied_count
    elif isinstance(fixed_count, int):
        normalized["applied_count"] = fixed_count

    verified_count = raw.get("verified_count")
    if isinstance(verified_count, int):
        normalized["verified_count"] = verified_count

    unverified_count = raw.get("unverified_count")
    if isinstance(unverified_count, int):
        normalized["unverified_count"] = unverified_count

    ai_metrics = raw.get("ai_metrics")
    if isinstance(ai_metrics, dict):
        normalized["ai_metrics"] = copy.deepcopy(ai_metrics)

    # Pass through tool-specific metadata (e.g. osv-scanner suppressions)
    suppressions = raw.get("suppressions")
    if isinstance(suppressions, list):
        normalized["suppressions"] = [s for s in suppressions if isinstance(s, dict)]

    return normalized
