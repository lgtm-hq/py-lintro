"""AI output renderer for summaries, explanations, and fix suggestions.

Single module for all AI display rendering, supporting three environments:
- Terminal: Rich Panels with ``━`` section headers
- GitHub Actions: ``::group::`` / ``::endgroup::`` collapsible sections
- Markdown: ``<details><summary>`` collapsible sections
- JSON: Full data always included (handled separately in json_output)

Used by both ``chk`` (summaries, explanations) and ``fmt`` (fix suggestions).
"""

from __future__ import annotations

import io
import os
import re
from collections.abc import Sequence

from rich.console import Console, Group, RenderableType
from rich.markup import escape
from rich.panel import Panel

from lintro.ai.cost import format_cost, format_token_count
from lintro.ai.models import AIFixSuggestion, AISummary
from lintro.ai.paths import relative_path
from lintro.ai.validation import ValidationResult
from lintro.utils.console.constants import BORDER_LENGTH

# Pattern to strip leading number prefixes like "1. ", "2) " from AI responses
_LEADING_NUMBER_RE = re.compile(r"^\d+[\.\)]\s*")

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _is_github_actions() -> bool:
    """Check if running inside GitHub Actions.

    Returns:
        True if GITHUB_ACTIONS environment variable is set.
    """
    return os.environ.get("GITHUB_ACTIONS") == "true"


def _cost_str(
    input_tokens: int,
    output_tokens: int,
    cost: float,
) -> str:
    """Build a cost summary string for the section header.

    Args:
        input_tokens: Total input tokens consumed.
        output_tokens: Total output tokens generated.
        cost: Estimated cost in USD.

    Returns:
        Cost summary string, or empty if cost is zero.
    """
    if cost <= 0:
        return ""
    tokens = format_token_count(input_tokens + output_tokens)
    return f"   {tokens} tokens, est. {format_cost(cost)}"


def _print_section_header(
    console: Console,
    emoji: str,
    label: str,
    detail: str,
    *,
    cost_info: str = "",
) -> None:
    """Print a terminal section header with ━ bars.

    Args:
        console: Rich Console to print to.
        emoji: Section emoji (e.g. "🧠", "🤖").
        label: Tool or section name.
        detail: Summary text (e.g. "3 issues explained (2 codes)").
        cost_info: Optional cost/token info to include in header.
    """
    border = "━" * BORDER_LENGTH
    console.print()
    console.print(f"[cyan]{border}[/cyan]")
    console.print(f"[bold cyan]{emoji}  {label}[/bold cyan] — {detail}")
    if cost_info:
        console.print(f"[dim]{cost_info}[/dim]")
    console.print(f"[cyan]{border}[/cyan]")


def _print_code_panel(
    console: Console,
    *,
    code: str,
    index: int,
    total: int,
    count: int,
    count_label: str,
    content: RenderableType,
    tool_name: str = "",
) -> None:
    """Print a Rich Panel for one error-code group.

    Shared by both explanation and fix rendering to ensure
    consistent Panel styling across chk and fmt.

    Args:
        console: Rich Console to print to.
        code: Error code (e.g. "B101", "D107").
        index: 1-based group index.
        total: Total number of groups.
        count: Number of occurrences/files.
        count_label: Label for count (e.g. "occurrence", "file").
        content: Rich renderable content for the panel body.
        tool_name: Optional tool name to show next to the error code.
    """
    plural = "s" if count != 1 else ""
    tool_part = f"  [dim]{tool_name}[/dim]" if tool_name else ""
    title = (
        f"[bold cyan]\\[{index}/{total}][/bold cyan]  "
        f"[bold yellow]{code}[/bold yellow]{tool_part}  "
        f"[dim]({count} {count_label}{plural})[/dim]"
    )
    console.print(
        Panel(
            content,
            title=title,
            title_align="left",
            border_style="cyan",
            padding=(0, 1),
        ),
    )


# ---------------------------------------------------------------------------
# Fix suggestion rendering (fmt only)
# ---------------------------------------------------------------------------


def render_fixes_terminal(
    suggestions: Sequence[AIFixSuggestion],
    *,
    tool_name: str = "",
    show_cost: bool = True,
) -> str:
    """Render fix suggestions for terminal output.

    Uses Rich Panels per error-code group, matching the interactive
    fix review style.

    Args:
        suggestions: Fix suggestions to render.
        tool_name: Name of the tool these suggestions are for.
        show_cost: Whether to show cost estimates.

    Returns:
        Formatted string for terminal display.
    """
    if not suggestions:
        return ""

    buf = io.StringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        highlight=False,
        width=BORDER_LENGTH,
    )

    # Compute totals up front for the header
    count = len(suggestions)
    total_input = sum(s.input_tokens for s in suggestions)
    total_output = sum(s.output_tokens for s in suggestions)
    total_cost = sum(s.cost_estimate for s in suggestions)

    plural = "s" if count != 1 else ""
    label = tool_name or "AI FIX SUGGESTIONS"
    detail = f"{count} fix suggestion{plural}"
    cost_info = _cost_str(total_input, total_output, total_cost) if show_cost else ""

    _print_section_header(
        console,
        "🤖",
        label,
        detail,
        cost_info=cost_info,
    )

    # Group by code for Panel rendering
    from collections import defaultdict

    groups: dict[str, list[AIFixSuggestion]] = defaultdict(list)
    for s in suggestions:
        groups[s.code or "unknown"].append(s)

    total_groups = len(groups)
    for gi, (code, fixes) in enumerate(groups.items(), 1):
        parts: list[RenderableType] = []

        explanation = fixes[0].explanation or ""
        if explanation:
            parts.append(f"[cyan]{escape(explanation)}[/cyan]")

        for fix in fixes:
            loc = relative_path(fix.file)
            if fix.line:
                loc += f":{fix.line}"
            parts.append(
                Panel(
                    f"[green]{escape(loc)}[/green]",
                    border_style="dim",
                    padding=(0, 1),
                ),
            )

        content: RenderableType = (
            Group(*parts) if len(parts) > 1 else (parts[0] if parts else "")
        )
        # Use tool_name from first suggestion in group
        group_tool = fixes[0].tool_name if fixes else ""
        _print_code_panel(
            console,
            code=code,
            index=gi,
            total=total_groups,
            count=len(fixes),
            count_label="file",
            content=content,
            tool_name=group_tool,
        )

    return buf.getvalue()


def render_fixes_github(
    suggestions: Sequence[AIFixSuggestion],
    *,
    tool_name: str = "",
    show_cost: bool = True,
) -> str:
    """Render fix suggestions for GitHub Actions logs.

    Args:
        suggestions: Fix suggestions to render.
        tool_name: Name of the tool these suggestions are for.
        show_cost: Whether to show cost estimates.

    Returns:
        Formatted string with GitHub Actions group markers.
    """
    if not suggestions:
        return ""

    lines: list[str] = []
    total_cost = 0.0

    for fix in suggestions:
        total_cost += fix.cost_estimate
        loc = relative_path(fix.file)
        if fix.line:
            loc += f":{fix.line}"

        code_label = f" [{fix.code}]" if fix.code else ""
        tool_label = f" ({fix.tool_name})" if fix.tool_name else ""
        lines.append(f"::group::{loc}{code_label}{tool_label} — {fix.explanation}")

        if fix.diff:
            lines.append("```diff")
            lines.append(fix.diff)
            lines.append("```")

        lines.append(f"Confidence: {fix.confidence}")
        lines.append("::endgroup::")

    if show_cost and total_cost > 0:
        lines.append(f"AI fix cost estimate: {format_cost(total_cost)}")

    return "\n".join(lines)


def render_fixes_markdown(
    suggestions: Sequence[AIFixSuggestion],
    *,
    tool_name: str = "",
    show_cost: bool = True,
) -> str:
    """Render fix suggestions as Markdown with collapsible diffs.

    Args:
        suggestions: Fix suggestions to render.
        tool_name: Name of the tool these suggestions are for.
        show_cost: Whether to show cost estimates.

    Returns:
        Markdown-formatted string.
    """
    if not suggestions:
        return ""

    lines: list[str] = []
    label = f"{tool_name} — AI Fix Suggestions" if tool_name else "AI Fix Suggestions"
    lines.append(f"### {label}")
    lines.append("")

    total_cost = 0.0

    for fix in suggestions:
        total_cost += fix.cost_estimate
        loc = f"`{fix.file}"
        if fix.line:
            loc += f":{fix.line}"
        loc += "`"

        code_label = f" **[{fix.code}]**" if fix.code else ""
        tool_label = f" ({fix.tool_name})" if fix.tool_name else ""

        lines.append("<details>")
        lines.append(
            f"<summary>{loc}{code_label}{tool_label} — {fix.explanation}</summary>",
        )
        lines.append("")

        if fix.diff:
            lines.append("```diff")
            lines.append(fix.diff)
            lines.append("```")
            lines.append("")

        lines.append(f"Confidence: {fix.confidence}")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    if show_cost and total_cost > 0:
        lines.append(f"*AI fix cost estimate: {format_cost(total_cost)}*")

    return "\n".join(lines)


def render_fixes(
    suggestions: Sequence[AIFixSuggestion],
    *,
    tool_name: str = "",
    show_cost: bool = True,
) -> str:
    """Render fixes using the appropriate format for the environment.

    Args:
        suggestions: Fix suggestions to render.
        tool_name: Name of the tool these suggestions are for.
        show_cost: Whether to show cost estimates.

    Returns:
        Formatted fix string.
    """
    if _is_github_actions():
        return render_fixes_github(
            suggestions,
            tool_name=tool_name,
            show_cost=show_cost,
        )
    return render_fixes_terminal(
        suggestions,
        tool_name=tool_name,
        show_cost=show_cost,
    )


# ---------------------------------------------------------------------------
# Summary rendering
# ---------------------------------------------------------------------------


def render_summary_terminal(
    summary: AISummary,
    *,
    show_cost: bool = True,
) -> str:
    """Render AI summary for terminal output.

    Uses Rich Panels with a structured layout for overview,
    key patterns, priority actions, and effort estimate.

    Args:
        summary: AI summary to render.
        show_cost: Whether to show cost estimates.

    Returns:
        Formatted string for terminal display.
    """
    if not summary.overview:
        return ""

    buf = io.StringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        highlight=False,
        width=BORDER_LENGTH,
    )

    cost_info = (
        _cost_str(summary.input_tokens, summary.output_tokens, summary.cost_estimate)
        if show_cost
        else ""
    )

    _print_section_header(
        console,
        "🧠",
        "AI SUMMARY",
        "actionable insights",
        cost_info=cost_info,
    )

    parts: list[RenderableType] = []

    # Overview
    parts.append(f"[cyan]{escape(summary.overview)}[/cyan]")

    # Key patterns
    if summary.key_patterns:
        parts.append("")
        parts.append("[bold yellow]Key Patterns:[/bold yellow]")
        for pattern in summary.key_patterns:
            parts.append(f"  [yellow]•[/yellow] {escape(pattern)}")

    # Priority actions
    if summary.priority_actions:
        parts.append("")
        parts.append("[bold green]Priority Actions:[/bold green]")
        for i, action in enumerate(summary.priority_actions, 1):
            clean = _LEADING_NUMBER_RE.sub("", action)
            parts.append(f"  [green]{i}.[/green] {escape(clean)}")

    # Triage suggestions
    if summary.triage_suggestions:
        parts.append("")
        parts.append("[bold magenta]Triage — Consider Suppressing:[/bold magenta]")
        for suggestion in summary.triage_suggestions:
            clean = _LEADING_NUMBER_RE.sub("", suggestion)
            parts.append(f"  [magenta]~[/magenta] {escape(clean)}")

    # Effort estimate
    if summary.estimated_effort:
        parts.append("")
        parts.append(
            f"[dim]Estimated effort: {escape(summary.estimated_effort)}[/dim]",
        )

    content: RenderableType = Group(*parts) if len(parts) > 1 else parts[0]
    console.print(
        Panel(
            content,
            border_style="cyan",
            padding=(0, 1),
        ),
    )

    return buf.getvalue()


def render_summary_github(
    summary: AISummary,
    *,
    show_cost: bool = True,
) -> str:
    """Render AI summary for GitHub Actions logs.

    Args:
        summary: AI summary to render.
        show_cost: Whether to show cost estimates.

    Returns:
        Formatted string with GitHub Actions group markers.
    """
    if not summary.overview:
        return ""

    lines: list[str] = []
    lines.append("::group::AI Summary — actionable insights")
    lines.append("")
    lines.append(summary.overview)

    if summary.key_patterns:
        lines.append("")
        lines.append("Key Patterns:")
        for pattern in summary.key_patterns:
            lines.append(f"  • {pattern}")

    if summary.priority_actions:
        lines.append("")
        lines.append("Priority Actions:")
        for i, action in enumerate(summary.priority_actions, 1):
            clean = _LEADING_NUMBER_RE.sub("", action)
            lines.append(f"  {i}. {clean}")

    if summary.triage_suggestions:
        lines.append("")
        lines.append("Triage — Consider Suppressing:")
        for suggestion in summary.triage_suggestions:
            clean = _LEADING_NUMBER_RE.sub("", suggestion)
            lines.append(f"  ~ {clean}")

    if summary.estimated_effort:
        lines.append("")
        lines.append(f"Estimated effort: {summary.estimated_effort}")

    if show_cost and summary.cost_estimate > 0:
        lines.append("")
        lines.append(f"AI cost estimate: {format_cost(summary.cost_estimate)}")

    lines.append("::endgroup::")

    return "\n".join(lines)


def render_summary_markdown(
    summary: AISummary,
    *,
    show_cost: bool = True,
) -> str:
    """Render AI summary as Markdown with collapsible section.

    Args:
        summary: AI summary to render.
        show_cost: Whether to show cost estimates.

    Returns:
        Markdown-formatted string.
    """
    if not summary.overview:
        return ""

    lines: list[str] = []
    lines.append("### AI Summary")
    lines.append("")
    lines.append("<details>")
    lines.append("<summary><b>Actionable insights</b></summary>")
    lines.append("")
    lines.append(summary.overview)

    if summary.key_patterns:
        lines.append("")
        lines.append("**Key Patterns:**")
        lines.append("")
        for pattern in summary.key_patterns:
            lines.append(f"- {pattern}")

    if summary.priority_actions:
        lines.append("")
        lines.append("**Priority Actions:**")
        lines.append("")
        for i, action in enumerate(summary.priority_actions, 1):
            clean = _LEADING_NUMBER_RE.sub("", action)
            lines.append(f"{i}. {clean}")

    if summary.triage_suggestions:
        lines.append("")
        lines.append("**Triage — Consider Suppressing:**")
        lines.append("")
        for suggestion in summary.triage_suggestions:
            clean = _LEADING_NUMBER_RE.sub("", suggestion)
            lines.append(f"- {clean}")

    if summary.estimated_effort:
        lines.append("")
        lines.append(f"*Estimated effort: {summary.estimated_effort}*")

    lines.append("")
    lines.append("</details>")

    if show_cost and summary.cost_estimate > 0:
        lines.append("")
        lines.append(f"*AI cost estimate: {format_cost(summary.cost_estimate)}*")

    return "\n".join(lines)


def render_summary(
    summary: AISummary,
    *,
    show_cost: bool = True,
) -> str:
    """Render summary using the appropriate format for the environment.

    Args:
        summary: AI summary to render.
        show_cost: Whether to show cost estimates.

    Returns:
        Formatted summary string.
    """
    if _is_github_actions():
        return render_summary_github(summary, show_cost=show_cost)
    return render_summary_terminal(summary, show_cost=show_cost)


# ---------------------------------------------------------------------------
# Fix validation rendering
# ---------------------------------------------------------------------------


def render_validation_terminal(result: ValidationResult) -> str:
    """Render fix validation results for terminal output.

    Args:
        result: Validation result to render.

    Returns:
        Formatted string for terminal display.
    """
    buf = io.StringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        highlight=False,
        width=BORDER_LENGTH,
    )

    total = result.verified + result.unverified
    if total == 0:
        return ""

    parts: list[str] = []
    if result.verified:
        parts.append(f"[green]{result.verified} verified[/green]")
    if result.unverified:
        parts.append(f"[yellow]{result.unverified} unverified[/yellow]")

    console.print(f"  [bold]Fix validation:[/bold] {' · '.join(parts)}")

    for detail in result.details:
        console.print(f"    [yellow]![/yellow] {escape(detail)}")

    return buf.getvalue()


def render_validation(result: ValidationResult) -> str:
    """Render validation using the appropriate format for the environment.

    Args:
        result: Validation result to render.

    Returns:
        Formatted validation string.
    """
    return render_validation_terminal(result)
