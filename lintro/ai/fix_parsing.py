"""Response parsing and diff generation for AI fix suggestions.

Parses single and batch AI responses into AIFixSuggestion objects
and generates unified diffs between original and suggested code.
"""

from __future__ import annotations

import difflib
import json
from typing import TYPE_CHECKING

from loguru import logger

from lintro.ai.paths import relative_path

if TYPE_CHECKING:
    from lintro.ai.models import AIFixSuggestion


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
        Parsed AIFixSuggestion, or None if parsing fails.
    """
    from lintro.ai.models import AIFixSuggestion

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        logger.debug(f"Failed to parse AI fix response for {file_path}:{line}")
        return None

    if not isinstance(data, dict):
        logger.debug(f"AI fix response is not a JSON object for {file_path}:{line}")
        return None

    original = data.get("original_code", "")
    suggested = data.get("suggested_code", "")

    if not isinstance(original, str) or not isinstance(suggested, str):
        logger.debug(f"AI fix code fields are not strings for {file_path}:{line}")
        return None

    if not original or not suggested or original == suggested:
        return None

    diff = generate_diff(file_path, original, suggested)

    return AIFixSuggestion(
        file=file_path,
        line=line,
        code=code,
        original_code=original,
        suggested_code=suggested,
        diff=diff,
        explanation=data.get("explanation", ""),
        confidence=data.get("confidence", "medium"),
        risk_level=data.get("risk_level", ""),
    )


def parse_batch_response(
    content: str,
    file_path: str,
) -> list[AIFixSuggestion]:
    """Parse a batch AI response into a list of AIFixSuggestions.

    Args:
        content: Raw AI response content (expected JSON array).
        file_path: Path to the file.

    Returns:
        List of parsed AIFixSuggestions (may be empty on parse failure).
    """
    from lintro.ai.models import AIFixSuggestion

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        logger.debug(f"Failed to parse batch AI response for {file_path}")
        return []

    if not isinstance(data, list):
        logger.debug(f"Batch response is not an array for {file_path}")
        return []

    results: list[AIFixSuggestion] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        original = item.get("original_code", "")
        suggested = item.get("suggested_code", "")
        if not original or not suggested or original == suggested:
            continue
        line = item.get("line", 0)
        code = item.get("code", "")
        diff = generate_diff(file_path, original, suggested)
        results.append(
            AIFixSuggestion(
                file=file_path,
                line=line,
                code=code,
                original_code=original,
                suggested_code=suggested,
                diff=diff,
                explanation=item.get("explanation", ""),
                confidence=item.get("confidence", "medium"),
                risk_level=item.get("risk_level", ""),
            ),
        )
    return results
