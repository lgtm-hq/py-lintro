"""Execution of advisory (AI-finder) tools for ``lintro review``.

Advisory tools are plugins declaring
:attr:`~lintro.enums.execution_class.ExecutionClass.ADVISORY`. They are
excluded from ``lintro chk`` / ``lintro fmt`` — and therefore from the health
score — because their findings are nondeterministic opinions rather than rule
violations (#1308). This module is the counterpart runner that
``lintro review`` uses to execute them.

It is deliberately a thin, sequential runner rather than a second copy of the
full :mod:`lintro.utils.tool_executor` pipeline: advisory tools never fix,
never participate in post-checks, never feed the health score, and never
affect the exit code unless the user opts in with ``--fail-on-findings``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from loguru import logger

from lintro.config.config_loader import get_config
from lintro.enums.action import Action
from lintro.enums.advisory_tools_value import AdvisoryToolsValue
from lintro.models.core.tool_result import ToolResult
from lintro.tools import tool_manager
from lintro.utils.execution.tool_configuration import (
    SkippedTool,
    configure_tool_for_execution,
)
from lintro.utils.unified_config import UnifiedConfigManager

if TYPE_CHECKING:
    from lintro.config.lintro_config import LintroConfig

__all__ = [
    "AdvisorySelection",
    "advisory_findings_count",
    "advisory_results_to_payload",
    "get_advisory_tool_names",
    "render_advisory_results",
    "resolve_advisory_tools",
    "run_advisory_tools",
]


@dataclass(frozen=True)
class AdvisorySelection:
    """Outcome of resolving a requested advisory tool selection.

    Attributes:
        to_run: Registered names of advisory tools that should execute.
        skipped: Advisory tools excluded by configuration, with reasons.
    """

    to_run: list[str] = field(default_factory=list)
    skipped: list[SkippedTool] = field(default_factory=list)


def get_advisory_tool_names() -> list[str]:
    """Return the registered names of every advisory tool.

    Returns:
        Sorted advisory tool names (empty when none are registered).
    """
    return sorted(
        name
        for name, plugin in tool_manager.get_all_tools().items()
        if plugin.definition.is_advisory
    )


def resolve_advisory_tools(
    *,
    requested: str | None,
    lintro_config: LintroConfig | None = None,
) -> AdvisorySelection:
    """Resolve the advisory tools to run for a review invocation.

    Propagates the :class:`ValueError` raised by
    :func:`_resolve_advisory_name` when a requested name is unknown or names a
    deterministic tool.

    Args:
        requested: Raw ``--advisory-tools`` value. ``None`` or ``"all"``
            selects every advisory tool enabled in configuration, ``"none"``
            selects nothing, and a comma-separated list selects those tools
            explicitly (bypassing ``execution.enabled_tools`` but still
            honoring ``tools.<name>.enabled: false``).
        lintro_config: Optional config override; the global config is loaded
            when omitted.

    Returns:
        AdvisorySelection with the tools to run and those skipped.
    """
    config = lintro_config or get_config()
    advisory_names = get_advisory_tool_names()

    normalized = (requested or AdvisoryToolsValue.ALL).strip().lower()
    if normalized == AdvisoryToolsValue.NONE:
        return AdvisorySelection()

    skipped: list[SkippedTool] = []
    if normalized == AdvisoryToolsValue.ALL:
        to_run: list[str] = []
        for name in advisory_names:
            if config.is_tool_enabled(name):
                to_run.append(name)
            else:
                skipped.append(SkippedTool(name=name, reason="disabled in config"))
        return AdvisorySelection(to_run=to_run, skipped=skipped)

    to_run = []
    for raw_name in normalized.split(","):
        name = raw_name.strip()
        if not name:
            continue
        resolved = _resolve_advisory_name(name=name, advisory_names=advisory_names)
        # Explicit selection bypasses execution.enabled_tools, mirroring how
        # ``chk --tools`` treats the allowlist, but a per-tool
        # ``enabled: false`` still wins.
        if not config.get_tool_config(resolved).enabled:
            skipped.append(SkippedTool(name=resolved, reason="disabled in config"))
            continue
        to_run.append(resolved)
    return AdvisorySelection(to_run=to_run, skipped=skipped)


def _resolve_advisory_name(*, name: str, advisory_names: list[str]) -> str:
    """Resolve a user-supplied advisory tool name to its registered key.

    Args:
        name: Lowercased user-supplied tool name.
        advisory_names: Registered advisory tool names.

    Returns:
        The matching registered advisory tool name.

    Raises:
        ValueError: If the name is unknown or names a deterministic tool.
    """
    candidates = {name, name.replace("_", "-"), name.replace("-", "_")}
    for candidate in candidates:
        if candidate in advisory_names:
            return candidate
    if any(tool_manager.is_tool_registered(candidate) for candidate in candidates):
        raise ValueError(
            f"Tool '{name}' is not an advisory tool; run it with "
            f"'lintro chk --tools {name}'.",
        )
    raise ValueError(
        f"Unknown advisory tool '{name}'. Available advisory tools: "
        f"{advisory_names or ['none']}",
    )


def run_advisory_tools(
    *,
    paths: list[str],
    tool_names: list[str],
    tool_options: str | None = None,
    lintro_config: LintroConfig | None = None,
) -> list[ToolResult]:
    """Execute the given advisory tools over ``paths``.

    Args:
        paths: File or directory paths to review.
        tool_names: Registered advisory tool names to execute.
        tool_options: Raw ``--tool-options`` string
            (``tool:option=value,...``) applied on top of configuration.
        lintro_config: Optional config override; the global config is loaded
            when omitted.

    Returns:
        One :class:`~lintro.models.core.tool_result.ToolResult` per tool, in
        the order given. A tool that raises is reported as a failed result
        rather than aborting the review.
    """
    if not tool_names or not paths:
        return []

    from lintro.utils.tool_options import parse_tool_options

    config = lintro_config or get_config()
    config_manager = UnifiedConfigManager()
    tool_option_dict = parse_tool_options(tool_options)
    results: list[ToolResult] = []
    for tool_name in tool_names:
        # Lookup and configuration are inside the guard too: a plugin that
        # raises while building its option state must not abort the whole
        # review any more than one that raises inside check().
        try:
            tool = configure_tool_for_execution(
                tool=tool_manager.get_tool(tool_name),
                tool_name=tool_name,
                config_manager=config_manager,
                tool_option_dict=tool_option_dict,
                exclude=None,
                include_venv=False,
                incremental=False,
                action=Action.CHECK,
                post_tools=set(),
                lintro_config=config,
            )
            results.append(tool.check(paths, {}))
        except Exception as exc:  # noqa: BLE001 - advisory runs never abort review
            logger.warning("[{}] advisory tool failed: {}", tool_name, exc)
            results.append(
                ToolResult(
                    name=tool_name,
                    success=False,
                    output=f"{tool_name} failed: {exc}",
                ),
            )
    return results


def advisory_findings_count(results: list[ToolResult]) -> int:
    """Return the total number of advisory findings across results.

    Args:
        results: Advisory tool results.

    Returns:
        Sum of the per-tool issue counts, ignoring skipped tools.
    """
    return sum(
        result.issues_count or 0
        for result in results
        if not getattr(result, "skipped", False)
    )


def render_advisory_results(
    *,
    results: list[ToolResult],
    output_format: str = "grid",
) -> str:
    """Render advisory results as a human-readable block.

    Args:
        results: Advisory tool results to render.
        output_format: Table format understood by
            :func:`lintro.utils.output.file_writer.format_tool_output`.

    Returns:
        Rendered text, or an empty string when there is nothing to show.
    """
    from lintro.utils.output.file_writer import format_tool_output

    if not results:
        return ""

    blocks: list[str] = []
    for result in results:
        header = f"Advisory: {result.name}"
        if getattr(result, "skipped", False):
            reason = result.skip_reason or "skipped"
            blocks.append(f"{header}\n  skipped — {reason}")
            continue
        body = format_tool_output(
            tool_name=result.name,
            output=result.output or "",
            output_format=output_format,
            issues=result.issues,
            success=result.success,
            issues_count=result.issues_count,
        )
        blocks.append(f"{header}\n{body}".rstrip())
    return "\n\n".join(blocks)


def advisory_results_to_payload(results: list[ToolResult]) -> list[dict[str, object]]:
    """Convert advisory results to JSON-serializable dictionaries.

    Args:
        results: Advisory tool results.

    Returns:
        One dictionary per tool with its findings as display rows.
    """
    payload: list[dict[str, object]] = []
    for result in results:
        payload.append(
            {
                "tool": result.name,
                "skipped": bool(getattr(result, "skipped", False)),
                "skip_reason": result.skip_reason,
                "issues_count": result.issues_count or 0,
                "issues": [issue.to_display_row() for issue in (result.issues or [])],
            },
        )
    return payload
