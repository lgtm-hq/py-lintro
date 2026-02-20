"""Fix validation via tool re-run.

After AI fixes are applied, re-runs the relevant linting tools on the
affected files to confirm that the issues were actually resolved.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from lintro.ai.models import AIFixSuggestion


IssueMatchKey = tuple[str, str, int | None]


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
    verified_by_tool: dict[str, int] = field(default_factory=dict)
    unverified_by_tool: dict[str, int] = field(default_factory=dict)


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

        # Build a multiset for accurate one-to-one matching.
        remaining_counts: Counter[IssueMatchKey] = Counter()
        for issue in remaining_issues:
            code = getattr(issue, "code", "") or ""
            remaining_path = _normalize_file_path(getattr(issue, "file", ""))
            line = _normalize_line(getattr(issue, "line", None))
            remaining_counts[(remaining_path, code, line)] += 1

        # Check each applied suggestion against remaining issues
        for s in suggestions:
            suggestion_path = _normalize_file_path(s.file)
            suggestion_line = _normalize_line(s.line)
            if _consume_matching_remaining_issue(
                remaining_counts=remaining_counts,
                file_path=suggestion_path,
                code=s.code,
                line=suggestion_line,
            ):
                result.unverified += 1
                result.unverified_by_tool[tool_name] = (
                    result.unverified_by_tool.get(tool_name, 0) + 1
                )
                rel = s.file.rsplit("/", 1)[-1]
                result.details.append(
                    f"[{s.code}] {rel}:{s.line} — issue still present",
                )
            else:
                result.verified += 1
                result.verified_by_tool[tool_name] = (
                    result.verified_by_tool.get(tool_name, 0) + 1
                )

    return result


def _normalize_line(line: object) -> int | None:
    """Normalize line values for reliable issue matching.

    ``BaseIssue.line`` is typed as ``int`` (default 0), so the ``str``
    branch is unnecessary.  The ``bool`` guard remains because ``bool``
    is a subclass of ``int`` in Python.
    """
    if isinstance(line, bool):
        return None
    if isinstance(line, int):
        return line if line > 0 else None
    return None


def _consume_matching_remaining_issue(
    *,
    remaining_counts: Counter[IssueMatchKey],
    file_path: str,
    code: str,
    line: int | None,
) -> bool:
    """Consume a matching remaining issue if present.

    Matching order:
    1. Exact file/code/line.
    2. File/code where the remaining issue has no line number.
    3. For line-less suggestions, file/code with any line.
    """
    if line is not None:
        exact_key = (file_path, code, line)
        if remaining_counts.get(exact_key, 0) > 0:
            remaining_counts[exact_key] -= 1
            return True

    unknown_line_key = (file_path, code, None)
    if remaining_counts.get(unknown_line_key, 0) > 0:
        remaining_counts[unknown_line_key] -= 1
        return True

    if line is None:
        for key in list(remaining_counts.keys()):
            if remaining_counts[key] <= 0:
                continue
            if key[0] == file_path and key[1] == code:
                remaining_counts[key] -= 1
                return True

    return False


def _normalize_file_path(file_path: str) -> str:
    """Normalize file paths for reliable issue matching."""
    if not file_path:
        return ""
    try:
        return str(Path(file_path).resolve())
    except OSError:
        return str(Path(file_path).absolute())


def _run_tool_check(
    tool_name: str,
    file_paths: list[str],
) -> list[object] | None:
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
