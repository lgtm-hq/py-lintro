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
from lintro.ai.enums import RiskLevel
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

    if tool_name:
        lines.append(f"### AI Fix Suggestions ({tool_name})")
        lines.append("")

    total_cost = 0.0

    for fix in suggestions:
        total_cost += fix.cost_estimate
        loc = relative_path(fix.file)
        if fix.line:
            loc += f":{fix.line}"

        code_label = f" [{_escape_annotation(fix.code)}]" if fix.code else ""
        tool_label = f" ({_escape_annotation(fix.tool_name)})" if fix.tool_name else ""
        escaped_loc = _escape_annotation(loc)
        escaped_explanation = _escape_annotation(fix.explanation or "")
        lines.append(
            f"::group::{escaped_loc}{code_label}{tool_label}"
            f" \u2014 {escaped_explanation}",
        )

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
        rel = html.escape(relative_path(fix.file))
        loc = f"`{rel}"
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


def _risk_to_annotation_level(risk_level: str) -> str:
    """Map AI risk level to a GitHub Actions annotation level.

    Args:
        risk_level: Risk classification from the AI fix suggestion
            (e.g. ``"behavioral-risk"``, ``"safe-style"``).

    Returns:
        One of ``"error"``, ``"warning"``, or ``"notice"``.
    """
    normalized = risk_level.lower().strip() if risk_level else ""
    try:
        return RiskLevel(normalized).to_severity_label(sarif=False)
    except ValueError:
        pass
    if normalized in {"high", "critical"}:
        return "error"
    if normalized in {"medium"}:
        return "warning"
    if normalized in {"low"}:
        return "notice"
    return "warning"


def _escape_annotation(value: str) -> str:
    """Escape special characters for GitHub Actions annotation messages.

    Args:
        value: Raw string to escape.

    Returns:
        Escaped string safe for workflow command messages.
    """
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(value: str) -> str:
    """Escape a value for use inside a GitHub Actions annotation property.

    Property values are delimited by ``,`` and terminated by ``::`` so
    both characters must be percent-encoded in addition to the standard
    message escapes.

    Args:
        value: Raw property value.

    Returns:
        Escaped string safe for annotation property positions.
    """
    return _escape_annotation(value).replace(",", "%2C").replace(":", "%3A")


def render_fixes_annotations(suggestions: Sequence[AIFixSuggestion]) -> str:
    """Emit GitHub Actions annotation commands for fix suggestions.

    Each suggestion maps its ``risk_level`` to the appropriate annotation
    level (``::error``, ``::warning``, or ``::notice``) and includes file,
    line, and title properties so annotations appear inline on PR diffs.

    Mapping:
        - ``high`` / ``critical`` -> ``::error``
        - ``medium`` / ``behavioral-risk`` -> ``::warning``
        - ``low`` / ``safe-style`` -> ``::notice``
        - (unset) -> ``::warning`` (default)

    Args:
        suggestions: Fix suggestions to annotate.

    Returns:
        Newline-joined annotation commands, or empty string if no
        suggestions are provided.
    """
    lines: list[str] = []
    for s in suggestions:
        level = _risk_to_annotation_level(s.risk_level)

        props: list[str] = []
        if s.file:
            props.append(f"file={_escape_property(s.file)}")
        if s.line:
            props.append(f"line={s.line}")

        title_parts: list[str] = []
        if s.tool_name:
            title_parts.append(s.tool_name)
        if s.code:
            if title_parts:
                title_parts[-1] += f"({s.code})"
            else:
                title_parts.append(s.code)
        if title_parts:
            props.append(f"title={_escape_property(title_parts[0])}")

        props_str = ",".join(props)

        explanation = s.explanation or "AI fix available"
        code_label = f" [{s.code}]" if s.code else ""
        confidence_label = f" (confidence: {s.confidence})" if s.confidence else ""
        msg = _escape_annotation(
            f"AI fix available{code_label}: {explanation}{confidence_label}",
        )

        if props_str:
            lines.append(f"::{level} {props_str}::{msg}")
        else:
            lines.append(f"::{level}::{msg}")
    return "\n".join(lines)


def render_fixes(
    suggestions: Sequence[AIFixSuggestion],
    *,
    tool_name: str = "",
    show_cost: bool = True,
    output_format: str = "auto",
) -> str:
    """Render fixes using the appropriate format for the environment.

    This is the public dispatcher for fix rendering, available for use
    by future pipeline integrations. Currently used by the interactive
    review loop and display modules.

    When running inside GitHub Actions (auto-detected), annotations are
    appended to the rendered output so they appear as inline warnings in
    the Actions UI.

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
        rendered = render_fixes_github(
            suggestions,
            tool_name=tool_name,
            show_cost=show_cost,
        )
        annotations = render_fixes_annotations(suggestions)
        if annotations:
            rendered = rendered + "\n" + annotations if rendered else annotations
        return rendered
    return render_fixes_terminal(
        suggestions,
        tool_name=tool_name,
        show_cost=show_cost,
    )
