"""Helper functions for output formatting.

This module contains escape and sanitization helpers used by
output writing classes and functions.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

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


def render_markdown_notes(result: ToolResult) -> list[str]:
    """Render the notes a tool attached to its result as Markdown.

    Args:
        result: The tool result.

    Returns:
        A quoted line and a blank line per note; none without notes.
    """
    lines: list[str] = []
    for note in tool_notes(getattr(result, "metadata", None)):
        lines.extend((f"> **Note:** {markdown_escape(note['message'])}", ""))
    return lines
