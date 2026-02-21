"""Summary rendering for terminal, GitHub Actions, and Markdown."""

from __future__ import annotations

import io

from rich.console import Console, Group, RenderableType
from rich.markup import escape
from rich.panel import Panel

from lintro.ai.cost import format_cost
from lintro.ai.display.shared import (
    LEADING_NUMBER_RE,
    cost_str,
    is_github_actions,
    print_section_header,
)
from lintro.ai.models import AISummary
from lintro.utils.console.constants import BORDER_LENGTH


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
        cost_str(summary.input_tokens, summary.output_tokens, summary.cost_estimate)
        if show_cost
        else ""
    )

    print_section_header(
        console,
        "\U0001f9e0",
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
            parts.append(f"  [yellow]\u2022[/yellow] {escape(pattern)}")

    # Priority actions
    if summary.priority_actions:
        parts.append("")
        parts.append("[bold green]Priority Actions:[/bold green]")
        for i, action in enumerate(summary.priority_actions, 1):
            clean = LEADING_NUMBER_RE.sub("", action)
            parts.append(f"  [green]{i}.[/green] {escape(clean)}")

    # Triage suggestions
    if summary.triage_suggestions:
        parts.append("")
        parts.append("[bold magenta]Triage \u2014 Consider Suppressing:[/bold magenta]")
        for suggestion in summary.triage_suggestions:
            clean = LEADING_NUMBER_RE.sub("", suggestion)
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
    lines.append("::group::AI Summary \u2014 actionable insights")
    lines.append("")
    lines.append(summary.overview)

    if summary.key_patterns:
        lines.append("")
        lines.append("Key Patterns:")
        for pattern in summary.key_patterns:
            lines.append(f"  \u2022 {pattern}")

    if summary.priority_actions:
        lines.append("")
        lines.append("Priority Actions:")
        for i, action in enumerate(summary.priority_actions, 1):
            clean = LEADING_NUMBER_RE.sub("", action)
            lines.append(f"  {i}. {clean}")

    if summary.triage_suggestions:
        lines.append("")
        lines.append("Triage \u2014 Consider Suppressing:")
        for suggestion in summary.triage_suggestions:
            clean = LEADING_NUMBER_RE.sub("", suggestion)
            lines.append(f"  ~ {clean}")

    if summary.estimated_effort:
        lines.append("")
        lines.append(f"Estimated effort: {summary.estimated_effort}")

    if show_cost and summary.cost_estimate > 0:
        lines.append("")
        lines.append(f"AI cost: {format_cost(summary.cost_estimate)}")

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
            clean = LEADING_NUMBER_RE.sub("", action)
            lines.append(f"{i}. {clean}")

    if summary.triage_suggestions:
        lines.append("")
        lines.append("**Triage \u2014 Consider Suppressing:**")
        lines.append("")
        for suggestion in summary.triage_suggestions:
            clean = LEADING_NUMBER_RE.sub("", suggestion)
            lines.append(f"- {clean}")

    if summary.estimated_effort:
        lines.append("")
        lines.append(f"*Estimated effort: {summary.estimated_effort}*")

    lines.append("")
    lines.append("</details>")

    if show_cost and summary.cost_estimate > 0:
        lines.append("")
        lines.append(f"*AI cost: {format_cost(summary.cost_estimate)}*")

    return "\n".join(lines)


def render_summary(
    summary: AISummary,
    *,
    show_cost: bool = True,
    output_format: str = "auto",
) -> str:
    """Render summary using the appropriate format for the environment.

    Args:
        summary: AI summary to render.
        show_cost: Whether to show cost estimates.
        output_format: Output format -- ``"auto"`` (default) selects
            terminal or GitHub Actions based on environment,
            ``"markdown"`` uses Markdown with collapsible section.

    Returns:
        Formatted summary string.
    """
    if output_format == "markdown":
        return render_summary_markdown(summary, show_cost=show_cost)
    if is_github_actions():
        return render_summary_github(summary, show_cost=show_cost)
    return render_summary_terminal(summary, show_cost=show_cost)
