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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lintro.models.core.tool_result import ToolResult

#: ``ToolResult.metadata`` key for notes about a complete result: things the
#: reader should know that are neither findings nor a failure, such as
#: osv-scanner's unresolved indirect dependencies. Each entry is a dict with
#: ``summary`` (a few words, for a summary table) and ``message``.
NOTES_KEY: str = "notes"

#: ``ToolResult.metadata`` key naming a below-minimum version that
#: ``LINTRO_ALLOW_VERSION_LAG`` let run (#3556): ``version``, ``minimum`` and
#: ``allowed_by``, so a JSON reader need not parse the note text.
VERSION_LAG_KEY: str = "version_lag"


@dataclass(frozen=True)
class ToolNote:
    """One note about a tool's result.

    Attributes:
        summary: A few words for a summary table's Notes column.
        message: The full note.
    """

    summary: str
    message: str


def with_tool_notes(
    metadata: Mapping[str, Any],
    notes: Sequence[ToolNote],
) -> dict[str, Any]:
    """Return metadata with notes added under :data:`NOTES_KEY`.

    Args:
        metadata: The result's metadata.
        notes: Notes to add; none leaves the metadata unchanged.

    Returns:
        A new metadata dict.
    """
    merged = dict(metadata)
    if notes:
        merged[NOTES_KEY] = [
            *tool_notes(metadata),
            *({"summary": n.summary, "message": n.message} for n in notes),
        ]
    return merged


def tool_notes(metadata: Mapping[str, Any] | None) -> list[dict[str, str]]:
    """Return the well-formed notes in a result's metadata.

    Args:
        metadata: The result's metadata, if any.

    Returns:
        Each note as ``{"summary": ..., "message": ...}``, in order.
    """
    raw = metadata.get(NOTES_KEY) if isinstance(metadata, Mapping) else None
    if not isinstance(raw, list):
        return []
    return [
        {"summary": note["summary"], "message": note["message"]}
        for note in raw
        if isinstance(note, Mapping)
        and isinstance(note.get("summary"), str)
        and isinstance(note.get("message"), str)
        and note["message"].strip()
    ]


def get_ai_count(result: ToolResult, key: str) -> int:
    """Get an integer AI metadata count from a result object.

    Falls back from ``applied_count`` to ``fixed_count`` for
    backward compatibility with older metadata.

    Args:
        result: Tool result carrying metadata.
        key: Metadata key to read (e.g. ``"applied_count"``).

    Returns:
        Non-negative integer count, or ``0`` when absent/invalid.
    """
    metadata = result.metadata
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

    notes = tool_notes(raw)
    if notes:
        normalized[NOTES_KEY] = notes

    version_lag = raw.get(VERSION_LAG_KEY)
    if isinstance(version_lag, dict):
        normalized[VERSION_LAG_KEY] = dict(version_lag)

    return normalized
