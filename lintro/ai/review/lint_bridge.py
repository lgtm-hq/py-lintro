"""Lint integration bridge for AI diff review."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from lintro.ai.prompts.review import format_lint_results_section
from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import resolve_issue_code
from lintro.tools import tool_manager
from lintro.utils.execution.tool_configuration import (
    configure_tool_for_execution,
    get_tools_to_run,
)
from lintro.utils.unified_config import UnifiedConfigManager

if TYPE_CHECKING:
    from lintro.config.lintro_config import LintroConfig

__all__ = [
    "format_lint_results_for_prompt",
    "run_lint_on_changed_files",
]


def run_lint_on_changed_files(
    *,
    changed_files: list[str],
    lintro_config: LintroConfig,
) -> list[ToolResult]:
    """Run lintro check tools scoped to changed files without AI enhancement.

    Args:
        changed_files: Repository-relative changed file paths.
        lintro_config: Loaded Lintro configuration.

    Returns:
        Raw tool results from applicable linters.
    """
    if not changed_files:
        return []

    selection = get_tools_to_run(
        tools="all",
        action=Action.CHECK,
        lintro_config=lintro_config,
    )
    if not selection.to_run:
        return []

    config_manager = UnifiedConfigManager()
    results: list[ToolResult] = []

    for tool_name in selection.to_run:
        try:
            tool = tool_manager.get_tool(tool_name)
        except (KeyError, ValueError):
            continue

        try:
            tool = configure_tool_for_execution(
                tool=tool,
                tool_name=tool_name,
                config_manager=config_manager,
                tool_option_dict={},
                exclude=None,
                include_venv=False,
                incremental=False,
                action=Action.CHECK,
                post_tools=set(),
                auto_install=False,
                lintro_config=lintro_config,
            )
            result = tool.check(paths=changed_files, options={})
        except (
            Exception
        ):  # noqa: BLE001 - one tool failure must not abort scoped lint run
            logger.warning(
                "Lint bridge skipped {} after check failure",
                tool_name,
                exc_info=True,
            )
            continue
        results.append(result)

    return results


def format_lint_results_for_prompt(
    *,
    results: list[ToolResult],
    max_entries: int = 200,
) -> str:
    """Format lint tool results as a compact prompt digest.

    Args:
        results: Tool results from ``run_lint_on_changed_files``.
        max_entries: Maximum number of issue entries to include.

    Returns:
        Lint digest wrapped in ``<lint_results>`` tags, or empty string.
    """
    lines: list[str] = []
    for result in results:
        if not result.issues:
            continue
        for issue in result.issues:
            if len(lines) >= max_entries:
                break
            code = resolve_issue_code(issue) or "unknown"
            message = getattr(issue, "message", "") or ""
            file_path = getattr(issue, "file", "") or ""
            line_no = getattr(issue, "line", None)
            line_suffix = f" | line: {line_no}" if line_no else ""
            lines.append(
                f"Tool: {result.name} | file: {file_path}{line_suffix}\n"
                f"Code: {code} | Message: {message}",
            )
        if len(lines) >= max_entries:
            break

    if not lines:
        return ""

    digest = "\n\n".join(lines)
    if len(lines) >= max_entries:
        digest += "\n\n(truncated lint digest)"
    return format_lint_results_section(digest=digest)
