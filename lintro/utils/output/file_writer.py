"""File writing and output formatting functions.

This module provides functions for writing tool results to files
and formatting tool output for display.
"""

# mypy: ignore-errors
# Note: mypy errors are suppressed because lintro runs mypy from file's directory,
# breaking package resolution. When run properly (mypy lintro/...), this file passes.

from __future__ import annotations

import csv
import datetime
import html
import json
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

# Import parser_registration to auto-register all parsers
import lintro.utils.output.parser_registration  # noqa: F401
from lintro.enums.action import Action
from lintro.enums.output_format import OutputFormat, normalize_output_format
from lintro.enums.tool_name import ToolName
from lintro.formatters.formatter import format_issues, format_issues_with_sections
from lintro.parsers.base_issue import BaseIssue
from lintro.utils.output.helpers import sanitize_csv_value
from lintro.utils.output.parser_registration import ParserError
from lintro.utils.output.parser_registry import ParserRegistry

try:
    import tabulate as _tabulate_module  # noqa: F401

    TABULATE_AVAILABLE = True
    del _tabulate_module
except ImportError:
    TABULATE_AVAILABLE = False

if TYPE_CHECKING:
    from lintro.models.core.tool_result import ToolResult


def build_doc_url_map(all_results: Sequence[Any]) -> dict[str, str]:
    """Build a mapping of rule codes to documentation URLs from results.

    Iterates all issues across results and collects non-empty doc_url
    values keyed by their code. Used by SARIF output to populate helpUri.

    Args:
        all_results: Sequence of ToolResult objects.

    Returns:
        Mapping of rule codes to documentation URLs (may be empty).
    """
    doc_url_map: dict[str, str] = {}
    for result in all_results:
        if hasattr(result, "issues") and result.issues:
            for issue in result.issues:
                code = str(getattr(issue, "code", "") or "")
                url = str(getattr(issue, "doc_url", "") or "")
                if code and url:
                    doc_url_map[code] = url
    return doc_url_map


def _result_has_fix_split(result: ToolResult, action: Action) -> bool:
    """Check whether a result should render with the fix-mode two-table split.

    Args:
        result: Tool result to check.
        action: The action being performed.

    Returns:
        True when action is FIX and pre-fix issues are available for display.
    """
    return (
        action == Action.FIX
        and getattr(result, "initial_issues", None) is not None
        and bool(result.initial_issues)
    )


def _render_markdown_issue_rows(issues: Sequence[BaseIssue]) -> list[str]:
    """Render issues as markdown table rows (without header).

    Args:
        issues: Issues to render.

    Returns:
        List of markdown table row strings.
    """
    rows: list[str] = []
    for issue in issues:
        file_val = str(getattr(issue, "file", "") or "").replace("|", r"\|")
        line_val = getattr(issue, "line", None) or 0
        code_val = str(getattr(issue, "code", "") or "").replace("|", r"\|")
        msg_val = str(getattr(issue, "message", "") or "").replace("|", r"\|")
        doc_url = str(getattr(issue, "doc_url", "") or "")
        doc_val = f"[docs]({doc_url})".replace("|", r"\|") if doc_url else ""
        rows.append(
            f"| {file_val} | {line_val} | {code_val} | {msg_val} | {doc_val} |",
        )
    return rows


def _render_html_issue_rows(issues: Sequence[BaseIssue]) -> list[str]:
    """Render issues as HTML table rows (without <table> wrapper).

    Args:
        issues: Issues to render.

    Returns:
        List of HTML ``<tr>`` strings.
    """
    rows: list[str] = []
    for issue in issues:
        f_val = html.escape(str(getattr(issue, "file", "") or ""))
        l_val = html.escape(str(getattr(issue, "line", None) or 0))
        c_val = html.escape(str(getattr(issue, "code", "") or ""))
        m_val = html.escape(str(getattr(issue, "message", "") or ""))
        doc_url = str(getattr(issue, "doc_url", "") or "")
        d_val = (
            f'<a href="{html.escape(doc_url, quote=True)}">docs</a>' if doc_url else ""
        )
        rows.append(
            f"<tr><td>{f_val}</td><td>{l_val}</td>"
            f"<td>{c_val}</td><td>{m_val}</td><td>{d_val}</td></tr>",
        )
    return rows


_MARKDOWN_ISSUES_HEADER = (
    "| File | Line | Code | Message | Docs |\n|------|------|------|---------|------|"
)

_HTML_ISSUES_HEADER = (
    "<table border='1'><tr><th>File</th><th>Line</th>"
    "<th>Code</th><th>Message</th><th>Docs</th></tr>"
)


def _serialize_issue(issue: BaseIssue) -> dict[str, Any]:
    """Serialize a BaseIssue to a JSON-safe dictionary.

    Args:
        issue: BaseIssue: The issue to serialize.

    Returns:
        dict[str, Any]: Serialized issue data.
    """
    data: dict[str, Any] = {
        "file": getattr(issue, "file", "") or "",
        "line": getattr(issue, "line", None) or 0,
        "code": getattr(issue, "code", "") or "",
        "message": getattr(issue, "message", "") or "",
    }
    doc_url = getattr(issue, "doc_url", "") or ""
    if doc_url:
        data["doc_url"] = doc_url
    return data


def write_output_file(
    *,
    output_path: str,
    output_format: OutputFormat,
    all_results: list[ToolResult],
    action: Action,
    total_issues: int,
    total_fixed: int,
) -> None:
    """Write results to user-specified output file.

    Args:
        output_path: str: Path to the output file.
        output_format: OutputFormat: Format for the output.
        all_results: list: List of ToolResult objects.
        action: Action: The action performed (check, fmt, test).
        total_issues: int: Total number of issues found.
        total_fixed: int: Total number of issues fixed.
    """
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    if output_format == OutputFormat.JSON:
        # Build JSON structure similar to stdout JSON mode
        json_data: dict[str, Any] = {
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            "action": action.value,
            "summary": {
                "total_issues": total_issues,
                "total_fixed": total_fixed,
                "tools_run": len(all_results),
            },
            "results": [],
        }
        for result in all_results:
            result_data = {
                "tool": result.name,
                "success": getattr(result, "success", True),
                "issues_count": getattr(result, "issues_count", 0),
                "output": getattr(result, "output", ""),
            }
            ai_metadata = getattr(result, "ai_metadata", None)
            if isinstance(ai_metadata, dict) and ai_metadata:
                from lintro.ai.metadata import normalize_ai_metadata

                normalized = normalize_ai_metadata(ai_metadata)
                if normalized:
                    result_data["ai_metadata"] = normalized
            if hasattr(result, "issues") and result.issues:
                result_data["issues"] = [
                    _serialize_issue(issue) for issue in result.issues
                ]
            json_data["results"].append(result_data)
        output_file.write_text(
            json.dumps(json_data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    elif output_format == OutputFormat.CSV:
        # Write CSV format
        rows: list[list[str]] = []
        header: list[str] = [
            "tool",
            "issues_count",
            "file",
            "line",
            "code",
            "message",
            "doc_url",
        ]
        for result in all_results:
            if hasattr(result, "issues") and result.issues:
                for issue in result.issues:
                    rows.append(
                        [
                            sanitize_csv_value(result.name),
                            sanitize_csv_value(
                                str(getattr(result, "issues_count", 0)),
                            ),
                            sanitize_csv_value(str(getattr(issue, "file", "") or "")),
                            sanitize_csv_value(
                                str(getattr(issue, "line", None) or 0),
                            ),
                            sanitize_csv_value(str(getattr(issue, "code", "") or "")),
                            sanitize_csv_value(
                                str(getattr(issue, "message", "") or ""),
                            ),
                            sanitize_csv_value(
                                str(getattr(issue, "doc_url", "") or ""),
                            ),
                        ],
                    )
            else:
                rows.append(
                    [
                        sanitize_csv_value(result.name),
                        sanitize_csv_value(str(getattr(result, "issues_count", 0))),
                        "",
                        "",
                        "",
                        "",
                        "",
                    ],
                )
        with output_file.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(rows)

    elif output_format == OutputFormat.MARKDOWN:
        # Write Markdown format
        lines: list[str] = ["# Lintro Report", ""]
        lines.append("## Summary\n")
        lines.append("| Tool | Issues |")
        lines.append("|------|--------|")
        for result in all_results:
            lines.append(f"| {result.name} | {getattr(result, 'issues_count', 0)} |")
        lines.append("")
        for result in all_results:
            issues_count = getattr(result, "issues_count", 0)
            lines.append(f"### {result.name} ({issues_count} issues)")
            if _result_has_fix_split(result, action):
                initial = list(result.initial_issues or [])
                lines.append(f"#### Detected issues ({len(initial)})")
                lines.append(_MARKDOWN_ISSUES_HEADER)
                lines.extend(_render_markdown_issue_rows(initial))
                lines.append("")
                remaining = list(result.issues or [])
                if remaining:
                    lines.append(f"#### Remaining issues ({len(remaining)})")
                    lines.append(_MARKDOWN_ISSUES_HEADER)
                    lines.extend(_render_markdown_issue_rows(remaining))
                    lines.append("")
                else:
                    lines.append("#### All issues were auto-fixed.")
                    lines.append("")
            elif hasattr(result, "issues") and result.issues:
                lines.append(_MARKDOWN_ISSUES_HEADER)
                lines.extend(_render_markdown_issue_rows(result.issues))
                lines.append("")
            else:
                lines.append("No issues found.\n")
        output_file.write_text("\n".join(lines), encoding="utf-8")

    elif output_format == OutputFormat.HTML:
        # Write HTML format
        html_lines: list[str] = [
            "<html><head><title>Lintro Report</title></head><body>",
        ]
        html_lines.append("<h1>Lintro Report</h1>")
        html_lines.append("<h2>Summary</h2>")
        html_lines.append("<table border='1'><tr><th>Tool</th><th>Issues</th></tr>")
        for result in all_results:
            safe_name = html.escape(result.name)
            html_lines.append(
                f"<tr><td>{safe_name}</td>"
                f"<td>{getattr(result, 'issues_count', 0)}</td></tr>",
            )
        html_lines.append("</table>")
        for result in all_results:
            issues_count = getattr(result, "issues_count", 0)
            html_lines.append(
                f"<h3>{html.escape(result.name)} ({issues_count} issues)</h3>",
            )
            if _result_has_fix_split(result, action):
                initial = list(result.initial_issues or [])
                html_lines.append(f"<h4>Detected issues ({len(initial)})</h4>")
                html_lines.append(_HTML_ISSUES_HEADER)
                html_lines.extend(_render_html_issue_rows(initial))
                html_lines.append("</table>")
                remaining = list(result.issues or [])
                if remaining:
                    html_lines.append(
                        f"<h4>Remaining issues ({len(remaining)})</h4>",
                    )
                    html_lines.append(_HTML_ISSUES_HEADER)
                    html_lines.extend(_render_html_issue_rows(remaining))
                    html_lines.append("</table>")
                else:
                    html_lines.append("<p>All issues were auto-fixed.</p>")
            elif hasattr(result, "issues") and result.issues:
                html_lines.append(_HTML_ISSUES_HEADER)
                html_lines.extend(_render_html_issue_rows(result.issues))
                html_lines.append("</table>")
            else:
                html_lines.append("<p>No issues found.</p>")
        html_lines.append("</body></html>")
        output_file.write_text("\n".join(html_lines), encoding="utf-8")

    elif output_format == OutputFormat.SARIF:
        from lintro.ai.output.sarif import write_sarif
        from lintro.ai.output.sarif_bridge import (
            suggestions_from_results,
            summary_from_results,
        )

        suggestions = suggestions_from_results(all_results)
        summary = summary_from_results(all_results)

        write_sarif(
            suggestions,
            summary,
            output_path=output_file,
            doc_urls=build_doc_url_map(all_results) or None,
        )

    else:
        # Plain or Grid format - write formatted text output
        from lintro.formatters.formatter import format_fix_results

        lines = [f"Lintro {action.value.capitalize()} Report", "=" * 40, ""]
        for result in all_results:
            issues_count = getattr(result, "issues_count", 0)
            lines.append(f"{result.name}: {issues_count} issues")
            if _result_has_fix_split(result, action):
                split_output = format_fix_results(
                    detected_issues=list(result.initial_issues or []),
                    remaining_issues=list(result.issues) if result.issues else None,
                    output_format=output_format,
                    tool_name=result.name,
                )
                if split_output and split_output.strip():
                    lines.append(split_output.strip())
            else:
                output_text = getattr(result, "output", "")
                if output_text and output_text.strip():
                    lines.append(output_text.strip())
            lines.append("")
        lines.append(f"Total Issues: {total_issues}")
        if action == Action.FIX:
            lines.append(f"Total Fixed: {total_fixed}")
        output_file.write_text("\n".join(lines), encoding="utf-8")


def format_tool_output(
    tool_name: str,
    output: str,
    output_format: str | OutputFormat = "grid",
    issues: Sequence[BaseIssue] | None = None,
) -> str:
    """Format tool output using the specified format.

    Args:
        tool_name: str: Name of the tool that generated the output.
        output: str: Raw output from the tool.
        output_format: str: Output format (plain, grid, markdown, html, json, csv).
        issues: Sequence[BaseIssue] | None: List of parsed issue objects (optional).

    Returns:
        str: Formatted output string.
    """
    output_format = normalize_output_format(output_format)

    # Pytest output is already formatted by build_output_with_failures
    # in pytest_output_processor.py, so return it directly
    if tool_name == ToolName.PYTEST:
        return output if output else ""

    # If parsed issues are provided, use the unified formatter
    if issues:
        # Get fixability predicate from registry (O(1) lookup)
        is_fixable = ParserRegistry.get_fixability_predicate(tool_name)

        if output_format != "json" and is_fixable is not None and TABULATE_AVAILABLE:
            # Use unified formatter with built-in fixable grouping
            return format_issues_with_sections(
                issues=issues,
                output_format=output_format,
                group_by_fixable=True,
                tool_name=tool_name,
            )

        # Use unified formatter for all issues
        return format_issues(issues=issues, output_format=output_format)

    if not output or not output.strip():
        return "No issues found."

    # Try to parse the output using registered parser (O(1) lookup)
    # Note: pytest output is already formatted by build_output_with_failures
    # in pytest_output_processor.py, so we skip re-parsing here
    try:
        parsed_issues = ParserRegistry.parse(tool_name, output)
    except ParserError as e:
        # Parsing failed - return error message with raw output for debugging
        return f"Error: {e}\n\nRaw output:\n{output}"

    if parsed_issues:
        return format_issues(issues=parsed_issues, output_format=output_format)

    # Fallback: return the raw output
    return output
