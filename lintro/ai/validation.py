"""Fix validation via tool re-run.

After AI fixes are applied, re-runs the relevant linting tools on the
affected files to confirm that the issues were actually resolved.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from lintro.ai.models import AIFixSuggestion


@dataclass
class ValidationResult:
    """Result of validating applied fixes by re-running tools.

    Attributes:
        verified: Number of fixes whose issues no longer appear.
        unverified: Number of fixes whose issues still appear.
        new_issues: Number of new issues introduced by the fixes.
        details: Per-file validation details.
    """

    verified: int = 0
    unverified: int = 0
    new_issues: int = 0
    details: list[str] = field(default_factory=list)


def validate_applied_fixes(
    applied_suggestions: Sequence[AIFixSuggestion],
) -> ValidationResult | None:
    """Re-run tools on files modified by AI fixes to verify correctness.

    Groups applied suggestions by tool, runs each tool's check on the
    affected files, and checks whether the originally reported issues
    are still present.

    Args:
        applied_suggestions: Suggestions that were successfully applied.

    Returns:
        ValidationResult summarizing what was verified, or None if
        validation could not run (e.g. no tools available).
    """
    if not applied_suggestions:
        return None

    # Group suggestions by tool_name → set of files
    by_tool: dict[str, list[AIFixSuggestion]] = defaultdict(list)
    for s in applied_suggestions:
        tool = s.tool_name or "unknown"
        by_tool[tool].append(s)

    result = ValidationResult()

    for tool_name, suggestions in by_tool.items():
        if tool_name == "unknown":
            continue

        file_paths = list({s.file for s in suggestions})
        remaining_issues = _run_tool_check(tool_name, file_paths)

        if remaining_issues is None:
            # Tool not available or check failed — skip validation
            logger.debug(f"Validation skipped for {tool_name}: tool check failed")
            continue

        # Build a set of (file, code) pairs from remaining issues
        remaining_set: set[tuple[str, str]] = set()
        for issue in remaining_issues:
            code = getattr(issue, "code", "") or ""
            remaining_set.add((issue.file, code))

        # Check each applied suggestion against remaining issues
        for s in suggestions:
            key = (s.file, s.code)
            if key in remaining_set:
                result.unverified += 1
                rel = s.file.rsplit("/", 1)[-1]
                result.details.append(
                    f"[{s.code}] {rel}:{s.line} — issue still present",
                )
            else:
                result.verified += 1

    return result


def _run_tool_check(
    tool_name: str,
    file_paths: list[str],
) -> list | None:
    """Run a tool's check on specific files.

    Args:
        tool_name: Name of the tool to run (e.g. "ruff").
        file_paths: Absolute paths to files to check.

    Returns:
        List of issues found, or None if the tool is not available
        or the check failed.
    """
    try:
        from lintro.tools import tool_manager

        tool = tool_manager.get_tool(tool_name)
    except (KeyError, ImportError):
        logger.debug(f"Validation: tool {tool_name!r} not available")
        return None

    try:
        tool_result = tool.check(file_paths, {})
        if tool_result.issues is not None:
            return list(tool_result.issues)
        return []
    except Exception:
        logger.debug(
            f"Validation: {tool_name} check failed",
            exc_info=True,
        )
        return None
