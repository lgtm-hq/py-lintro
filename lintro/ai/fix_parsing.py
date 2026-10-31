"""Response parsing and diff generation for AI fix suggestions.

Parses single and batch AI responses into AIFixSuggestion objects
and generates unified diffs between original and suggested code.
"""

from __future__ import annotations

import difflib
import json
from typing import TYPE_CHECKING, Any

from loguru import logger

from lintro.ai.cli_schemas import FIX_BATCH_KEY
from lintro.ai.enums import ConfidenceLevel, RiskLevel
from lintro.ai.exceptions import AIError
from lintro.ai.paths import relative_path

if TYPE_CHECKING:
    from lintro.ai.models import AIFixSuggestion


class AIFixParseError(AIError):
    """An AI fix response is malformed rather than a valid empty suggestion."""


def _optional_string(data: dict[str, Any], field: str, default: str = "") -> str:
    """Read optional string metadata, defaulting null and other non-strings.

    Args:
        data: Decoded fix entry.
        field: Optional metadata field name.
        default: Value for missing or non-string metadata.

    Returns:
        The string field value or its default.
    """
    value = data.get(field)
    return value if isinstance(value, str) else default


def generate_diff(
    file_path: str,
    original: str,
    suggested: str,
) -> str:
    """Generate a unified diff between original and suggested code.

    Args:
        file_path: Path for the diff header.
        original: Original code snippet.
        suggested: Suggested replacement.

    Returns:
        Unified diff string.
    """
    original_lines = original.splitlines()
    suggested_lines = suggested.splitlines()

    rel = relative_path(file_path)
    diff = difflib.unified_diff(
        original_lines,
        suggested_lines,
        fromfile=f"a/{rel}",
        tofile=f"b/{rel}",
    )
    return "\n".join(diff)


def _parse_suggestion(
    data: dict[str, Any],
    file_path: str,
    line: int,
    code: str,
) -> AIFixSuggestion | None:
    """Validate a fix entry, preserving empty patches as valid absence.

    Missing or non-string confidence defaults to medium; unknown labels
    stay low-confidence. Unrecognized risk stays unclassified.

    Args:
        data: Decoded fix entry.
        file_path: Path to the file being fixed.
        line: Line number of the issue.
        code: Error code of the issue.

    Returns:
        A validated suggestion, or None for an empty or no-op patch.

    Raises:
        AIFixParseError: Code fields are missing or are not strings.
    """
    from lintro.ai.models import AIFixSuggestion

    original = data.get("original_code")
    suggested = data.get("suggested_code")
    if not isinstance(original, str) or not isinstance(suggested, str):
        raise AIFixParseError(
            f"AI fix code fields must be strings for {file_path}:{line}",
        )
    if not original or not suggested:
        return None
    if original == suggested:
        logger.debug(f"AI suggested no-op fix for {file_path}:{line}")
        return None

    try:
        confidence = ConfidenceLevel(
            _optional_string(data, "confidence", "medium").strip().lower(),
        )
    except ValueError:
        confidence = ConfidenceLevel.LOW
    risk: RiskLevel | str
    try:
        risk = RiskLevel(_optional_string(data, "risk_level").strip().lower())
    except ValueError:
        risk = ""

    return AIFixSuggestion(
        file=file_path,
        line=line,
        code=code,
        original_code=original,
        suggested_code=suggested,
        diff=generate_diff(file_path, original, suggested),
        explanation=_optional_string(data, "explanation"),
        confidence=confidence,
        risk_level=risk,
    )


def parse_fix_response(
    content: str,
    file_path: str,
    line: int,
    code: str,
) -> AIFixSuggestion | None:
    """Parse an AI response into an AIFixSuggestion.

    Args:
        content: Raw AI response content.
        file_path: Path to the file.
        line: Line number of the issue.
        code: Error code of the issue.

    Returns:
        Parsed AIFixSuggestion, or None for a valid empty or no-op patch.

    Raises:
        AIFixParseError: The response is invalid JSON or has malformed fix fields.
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise AIFixParseError(
            f"Invalid JSON in AI fix response for {file_path}:{line}",
        ) from exc

    if not isinstance(data, dict):
        raise AIFixParseError(
            f"AI fix response must be a JSON object for {file_path}:{line}",
        )
    return _parse_suggestion(data, file_path, line, code)


def _batch_items(data: object, file_path: str) -> list[Any]:
    """Return the fix entries carried by a decoded batch payload.

    Args:
        data: Decoded batch response, either the wrapped object the CLI
            schema requests or a bare array.
        file_path: Path to the file, used for error context.

    Returns:
        The list of fix entries, including an explicitly empty list.

    Raises:
        AIFixParseError: The payload does not carry a batch array.
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        fixes = data.get(FIX_BATCH_KEY)
        if isinstance(fixes, list):
            return fixes
    raise AIFixParseError(
        f"Batch response must be an array or a {FIX_BATCH_KEY!r} array object "
        f"for {file_path}",
    )


def parse_batch_response(
    content: str,
    file_path: str,
) -> list[AIFixSuggestion]:
    """Parse a batch AI response into a list of AIFixSuggestions.

    Accepts the wrapped object the CLI transport now requests
    (``{"fixes": [...]}``, see :data:`lintro.ai.cli_schemas.FIX_BATCH_CLI_SCHEMA`)
    as well as a bare array, which older transcripts and cache entries hold.

    Args:
        content: Raw AI response content (a JSON object carrying ``fixes``, or
            a bare JSON array).
        file_path: Path to the file.

    Returns:
        List of parsed AIFixSuggestions, empty for valid absence of fixes.

    Raises:
        AIFixParseError: The JSON, batch container, or a fix entry is malformed.
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise AIFixParseError(
            f"Invalid JSON in batch AI response for {file_path}",
        ) from exc

    items = _batch_items(data, file_path)
    results: list[AIFixSuggestion] = []
    for item in items:
        if not isinstance(item, dict):
            raise AIFixParseError(
                f"Batch fix entry must be a JSON object for {file_path}",
            )
        raw_line = item.get("line", 0)
        try:
            line = int(raw_line) if not isinstance(raw_line, int) else raw_line
        except (TypeError, ValueError):
            line = 0
        raw_code = item.get("code", "")
        code = str(raw_code) if raw_code is not None else ""
        suggestion = _parse_suggestion(item, file_path, line, code)
        if suggestion is not None:
            results.append(suggestion)
    return results
