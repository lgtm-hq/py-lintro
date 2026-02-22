"""Fix suggestion rendering for terminal, GitHub Actions, and Markdown."""

from __future__ import annotations

import html
import io
from collections import defaultdict
from collections.abc import Sequence

from rich.console import Console, Group, RenderableType
from rich.markup import escape
from rich.panel import Panel

from lintro.ai.cost import format_cost
from lintro.ai.display.shared import (
    cost_str,
    is_github_actions,
    print_code_panel,
    print_section_header,
)
from lintro.ai.models import AIFixSuggestion
from lintro.ai.paths import relative_path
from lintro.utils.console.constants import BORDER_LENGTH


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
    cost_info = cost_str(total_input, total_output, total_cost) if show_cost else ""

    print_section_header(
        console,
        "\U0001f916",
        label,
        detail,
        cost_info=cost_info,
    )

    # Group by code for Panel rendering
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
        print_code_panel(
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
        lines.append(f"::group::{loc}{code_label}{tool_label} \u2014 {fix.explanation}")

        if fix.diff:
            sanitized_diff = fix.diff.replace("```", "``\u200b`")
            lines.append("```diff")
            lines.append(sanitized_diff)
            lines.append("```")

        lines.append(f"Confidence: {fix.confidence}")
        lines.append("::endgroup::")

    if show_cost and total_cost > 0:
        lines.append(f"AI cost: {format_cost(total_cost)}")

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
    label = (
        f"{tool_name} \u2014 AI Fix Suggestions" if tool_name else "AI Fix Suggestions"
    )
    lines.append(f"### {label}")
    lines.append("")

    total_cost = 0.0

    for fix in suggestions:
        total_cost += fix.cost_estimate
        loc = f"`{relative_path(fix.file)}"
        if fix.line:
            loc += f":{fix.line}"
        loc += "`"

        code_label = f" **[{html.escape(fix.code)}]**" if fix.code else ""
        tool_label = f" ({html.escape(fix.tool_name)})" if fix.tool_name else ""

        lines.append("<details>")
        escaped_explanation = html.escape(fix.explanation) if fix.explanation else ""
        summary_text = f"{loc}{code_label}{tool_label} \u2014 {escaped_explanation}"
        lines.append(f"<summary>{summary_text}</summary>")
        lines.append("")

        if fix.diff:
            sanitized_diff = fix.diff.replace("```", "``\u200b`")
            lines.append("```diff")
            lines.append(sanitized_diff)
            lines.append("```")
            lines.append("")

        lines.append(f"Confidence: {fix.confidence}")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    if show_cost and total_cost > 0:
        lines.append(f"*AI cost: {format_cost(total_cost)}*")

    return "\n".join(lines)


def render_fixes(
    suggestions: Sequence[AIFixSuggestion],
    *,
    tool_name: str = "",
    show_cost: bool = True,
    output_format: str = "auto",
) -> str:
    """Render fixes using the appropriate format for the environment.

    Args:
        suggestions: Fix suggestions to render.
        tool_name: Name of the tool these suggestions are for.
        show_cost: Whether to show cost estimates.
        output_format: Output format -- ``"auto"`` (default) selects
            terminal or GitHub Actions based on environment,
            ``"markdown"`` uses Markdown with collapsible diffs.

    Returns:
        Formatted fix string.
    """
    if output_format == "markdown":
        return render_fixes_markdown(
            suggestions,
            tool_name=tool_name,
            show_cost=show_cost,
        )
    if is_github_actions():
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
