"""Unified formatter for all tool issues.

This module provides a unified formatting approach that works with any
tool's issues by using the BaseIssue.to_display_row() method.

Instead of having tool-specific formatters, this module allows any issue
that inherits from BaseIssue to be formatted consistently.

Example:
    >>> from lintro.formatters.formatter import format_issues
    >>> from lintro.parsers.ruff.ruff_issue import RuffIssue
    >>>
    >>> issue = RuffIssue(file="foo.py", line=1, code="E501", message="Long")
    >>> issues = [issue]
    >>> output = format_issues(issues, output_format="grid")
    >>> print(output)
"""

from __future__ import annotations

from collections.abc import Sequence

from lintro.enums.display_column import STANDARD_COLUMNS, DisplayColumn
from lintro.enums.output_format import OutputFormat, normalize_output_format
from lintro.formatters.core.format_registry import TableDescriptor, get_style
from lintro.parsers.base_issue import BaseIssue
from lintro.utils.path_utils import normalize_file_path_for_display

# Map DisplayColumn enum to row dict keys
_COLUMN_KEY_MAP: dict[DisplayColumn, str] = {
    DisplayColumn.FILE: "file",
    DisplayColumn.LINE: "line",
    DisplayColumn.COLUMN: "column",
    DisplayColumn.CODE: "code",
    DisplayColumn.MESSAGE: "message",
    DisplayColumn.SEVERITY: "severity",
    DisplayColumn.FIXABLE: "fixable",
}


class UnifiedTableDescriptor(TableDescriptor):
    """Table descriptor that works with any BaseIssue subclass.

    Uses the to_display_row() method to extract data, making it
    compatible with all issue types.
    """

    def __init__(
        self,
        columns: list[DisplayColumn] | None = None,
    ) -> None:
        """Initialize the descriptor.

        Args:
            columns: Custom column list, or None to use STANDARD_COLUMNS.
        """
        self._columns = columns if columns is not None else STANDARD_COLUMNS

    def get_columns(self) -> list[str]:
        """Return the column names.

        Returns:
            List of column header names.
        """
        return [str(col) for col in self._columns]

    def get_rows(self, issues: Sequence[BaseIssue]) -> list[list[str]]:
        """Extract row data from issues using to_display_row().

        Args:
            issues: List of issues (any BaseIssue subclass).

        Returns:
            List of rows, each row being a list of column values.
        """
        rows: list[list[str]] = []

        for issue in issues:
            display_data = issue.to_display_row()

            # Normalize file path for display
            if "file" in display_data and display_data["file"]:
                display_data["file"] = normalize_file_path_for_display(
                    display_data["file"],
                )

            row = []
            for col in self._columns:
                key = _COLUMN_KEY_MAP.get(col, str(col).lower())
                value = display_data.get(key, "")
                row.append(str(value) if value else "")

            rows.append(row)

        return rows


def format_issues(
    issues: Sequence[BaseIssue],
    output_format: OutputFormat | str = OutputFormat.GRID,
    *,
    columns: list[DisplayColumn] | None = None,
    tool_name: str | None = None,
) -> str:
    """Format any issues using unified display.

    This function can format issues from any tool that uses BaseIssue,
    replacing the need for tool-specific formatters.

    Args:
        issues: List of issues (any BaseIssue subclass).
        output_format: Output format (grid, json, plain, etc.).
        columns: Custom column list (defaults to STANDARD_COLUMNS).
        tool_name: Tool name for JSON output.

    Returns:
        Formatted string.

    Example:
        >>> issues = [RuffIssue(file="foo.py", line=1, code="E501", message="Too long")]
        >>> print(format_issues(issues))
    """
    if not issues:
        return "No issues found."

    normalized_format = normalize_output_format(output_format)
    descriptor = UnifiedTableDescriptor(columns=columns)

    style = get_style(normalized_format)
    cols = descriptor.get_columns()
    rows = descriptor.get_rows(list(issues))

    return style.format(columns=cols, rows=rows, tool_name=tool_name)


def format_issues_with_sections(
    issues: Sequence[BaseIssue],
    output_format: OutputFormat | str = OutputFormat.GRID,
    *,
    group_by_fixable: bool = True,
    tool_name: str | None = None,
) -> str:
    """Format issues with optional fixable/non-fixable sections.

    This function groups issues by their fixable status and formats
    them in separate sections (except for JSON format).

    Args:
        issues: List of issues (any BaseIssue subclass).
        output_format: Output format (grid, json, plain, etc.).
        group_by_fixable: Whether to group by fixable status.
        tool_name: Tool name for JSON output.

    Returns:
        Formatted string with sections.

    Example:
        >>> print(format_issues_with_sections(issues, group_by_fixable=True))
        Auto-fixable issues
        ... table ...

        Not auto-fixable issues
        ... table ...
    """
    if not issues:
        return "No issues found."

    normalized_format = normalize_output_format(output_format)

    # JSON/GITHUB format: return single table for compatibility
    if (
        normalized_format in {OutputFormat.JSON, OutputFormat.GITHUB}
        or not group_by_fixable
    ):
        return format_issues(
            issues,
            output_format=normalized_format,
            tool_name=tool_name,
        )

    # Partition issues by fixable status
    fixable: list[BaseIssue] = []
    non_fixable: list[BaseIssue] = []

    for issue in issues:
        if getattr(issue, "fixable", False):
            fixable.append(issue)
        else:
            non_fixable.append(issue)

    sections: list[str] = []

    if fixable:
        fixable_output = format_issues(fixable, output_format=normalized_format)
        sections.append("Auto-fixable issues\n" + fixable_output)

    if non_fixable:
        non_fixable_output = format_issues(non_fixable, output_format=normalized_format)
        sections.append("Not auto-fixable issues\n" + non_fixable_output)

    if not sections:
        return "No issues found."

    return "\n\n".join(sections)


def format_fix_results(
    detected_issues: Sequence[BaseIssue],
    remaining_issues: Sequence[BaseIssue] | None,
    output_format: OutputFormat | str = OutputFormat.GRID,
    *,
    tool_name: str | None = None,
) -> str:
    """Format fix-mode results as two separate tables.

    Renders a "Detected issues" table and a "Remaining issues" table
    so users can clearly see what was auto-fixed vs what still needs
    attention. When all issues are fixed, the "Remaining" table is omitted.

    For JSON/GitHub formats, returns a single combined table for
    backward compatibility.

    Args:
        detected_issues: Issues found before fixes were applied.
        remaining_issues: Issues still present after fixes, or None/empty
            if all were fixed.
        output_format: Output format (grid, json, plain, etc.).
        tool_name: Tool name for JSON output.

    Returns:
        Formatted string with one or two labeled tables.
    """
    if not detected_issues:
        return "No issues found."

    normalized_format = normalize_output_format(output_format)

    # JSON/GitHub: combine both lists into a single output (no structural change)
    if normalized_format in {OutputFormat.JSON, OutputFormat.GITHUB}:
        all_issues = list(detected_issues)
        return format_issues(
            all_issues,
            output_format=normalized_format,
            tool_name=tool_name,
        )

    sections: list[str] = []

    # Always show detected issues
    detected_output = format_issues(
        detected_issues,
        output_format=normalized_format,
        tool_name=tool_name,
    )
    sections.append(f"Detected issues ({len(detected_issues)})\n{detected_output}")

    # Only show remaining if there are any
    if remaining_issues:
        remaining_output = format_issues(
            remaining_issues,
            output_format=normalized_format,
            tool_name=tool_name,
        )
        sections.append(
            f"Remaining issues ({len(remaining_issues)})\n{remaining_output}",
        )
    else:
        sections.append("All issues were auto-fixed.")

    return "\n\n".join(sections)


def format_tool_result(
    tool_name: str,
    issues: Sequence[BaseIssue],
    output_format: OutputFormat | str = OutputFormat.GRID,
    *,
    group_by_fixable: bool = False,
) -> str:
    """Format a tool's results with appropriate sections and metadata.

    This is a convenience function that combines formatting with
    tool-specific defaults.

    Args:
        tool_name: Name of the tool.
        issues: List of issues from the tool.
        output_format: Output format.
        group_by_fixable: Group by fixable status.

    Returns:
        Formatted string.
    """
    if group_by_fixable:
        return format_issues_with_sections(
            issues,
            output_format=output_format,
            group_by_fixable=True,
            tool_name=tool_name,
        )

    return format_issues(
        issues,
        output_format=output_format,
        tool_name=tool_name,
    )
