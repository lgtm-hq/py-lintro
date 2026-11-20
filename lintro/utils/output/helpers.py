"""Helper functions for output formatting.

This module contains escape and sanitization helpers used by
output writing classes and functions.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

from lintro.utils.result_reason import verdict_gap
from lintro.utils.tool_metadata import tool_notes

if TYPE_CHECKING:
    from lintro.models.core.tool_result import ToolResult


def markdown_escape(text: str) -> str:
    """Escape text for Markdown formatting.

    Args:
        text: str: Text to escape.

    Returns:
        str: Escaped text safe for Markdown.
    """
    return text.replace("|", r"\|").replace("\n", " ")


def html_escape(text: str) -> str:
    """Escape text for HTML formatting.

    Args:
        text: str: Text to escape.

    Returns:
        str: Escaped text safe for HTML.
    """
    return html.escape(text)


def sanitize_csv_value(value: str) -> str:
    """Sanitize CSV cell value to prevent formula injection.

    Prefixes values starting with '=', '+', '-', or '@' with a single quote
    to prevent spreadsheet applications from interpreting them as formulas.

    Args:
        value: str: The value to sanitize.

    Returns:
        str: Sanitized value with leading quote if needed.
    """
    if value and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def note_messages(result: ToolResult) -> list[str]:
    """Return the messages of the notes a tool attached to its result.

    Args:
        result: The tool result.

    Returns:
        One message per note, in order; none without notes.
    """
    return [note["message"] for note in tool_notes(getattr(result, "metadata", None))]


#: Shown in place of an issue count for a tool that did not run.
NOT_RUN: str = "did not run"


def says_no_issues(result: ToolResult) -> bool:
    """Whether a report may say "No issues found" for a result without issues.

    Args:
        result: The tool result.

    Returns:
        False when the tool did not run or did not complete: its silence is
        no clean verdict (:func:`verdict_gap`).
    """
    return verdict_gap(result) is None


def _gap_lines(result: ToolResult) -> list[str]:
    """Return the verdict-gap statement as one plain line, if there is one.

    Args:
        result: The tool result.

    Returns:
        ``["<headline> <reason>"]`` for a result that is no clean verdict,
        otherwise an empty list.
    """
    gap = verdict_gap(result)
    return [] if gap is None else [" ".join(gap)]


def render_markdown_notes(result: ToolResult) -> list[str]:
    """Render the notes a tool attached to its result as Markdown.

    Every report renders notes, so the statement that a result is no clean
    verdict (:func:`verdict_gap`) leads them: a tool that did not run or did
    not complete is never left to read as a pass (#3556).

    Args:
        result: The tool result.

    Returns:
        The verdict gap in bold, then a quoted line per note, each followed
        by a blank line; none for a complete result without notes.
    """
    lines: list[str] = []
    gap = verdict_gap(result)
    if gap is not None:
        lines.extend((f"**{gap[0]}** {markdown_escape(gap[1])}", ""))
    for message in note_messages(result):
        lines.extend((f"> **Note:** {markdown_escape(message)}", ""))
    return lines


def render_html_notes(result: ToolResult) -> list[str]:
    """Render the notes a tool attached to its result as HTML.

    Args:
        result: The tool result.

    Returns:
        The verdict gap (see :func:`render_markdown_notes`), then one escaped
        paragraph per note.
    """
    gap = verdict_gap(result)
    lead = (
        []
        if gap is None
        else [f"<p><strong>{html_escape(gap[0])}</strong> {html_escape(gap[1])}</p>"]
    )
    return lead + [
        f"<p><strong>Note:</strong> {html_escape(message)}</p>"
        for message in note_messages(result)
    ]


def render_plain_notes(result: ToolResult) -> list[str]:
    """Render the notes a tool attached to its result as plain text.

    Args:
        result: The tool result.

    Returns:
        The verdict gap (see :func:`render_markdown_notes`), then one line
        per note.
    """
    return _gap_lines(result) + [
        f"Note: {message}" for message in note_messages(result)
    ]


def csv_notes(result: ToolResult) -> str:
    """Return a result's notes as one CSV cell.

    Args:
        result: The tool result.

    Returns:
        The verdict gap (see :func:`render_markdown_notes`) and the note
        messages joined by newlines, sanitised; empty for a complete result
        without notes.
    """
    return sanitize_csv_value("\n".join(_gap_lines(result) + note_messages(result)))
