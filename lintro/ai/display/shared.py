"""Shared helpers for AI display rendering.

Cross-module utilities used by fix, summary, and validation renderers.
"""

from __future__ import annotations

import os
import re

from rich.console import Console, RenderableType
from rich.panel import Panel

from lintro.ai.cost import format_cost, format_token_count
from lintro.utils.console.constants import BORDER_LENGTH

# Pattern to strip leading number prefixes like "1. ", "2) " from AI responses
LEADING_NUMBER_RE = re.compile(r"^\d+[\.\)]\s*")


def is_github_actions() -> bool:
    """Check if running inside GitHub Actions.

    Returns:
        True if GITHUB_ACTIONS environment variable is set.
    """
    return os.environ.get("GITHUB_ACTIONS") == "true"


def cost_str(
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


def print_section_header(
    console: Console,
    emoji: str,
    label: str,
    detail: str,
    *,
    cost_info: str = "",
) -> None:
    """Print a terminal section header with bars.

    Args:
        console: Rich Console to print to.
        emoji: Section emoji (e.g. "brain", "robot").
        label: Tool or section name.
        detail: Summary text (e.g. "3 issues explained (2 codes)").
        cost_info: Optional cost/token info to include in header.
    """
    border = "\u2501" * BORDER_LENGTH
    console.print()
    console.print(f"[cyan]{border}[/cyan]")
    console.print(f"[bold cyan]{emoji}  {label}[/bold cyan] \u2014 {detail}")
    if cost_info:
        console.print(f"[dim]{cost_info}[/dim]")
    console.print(f"[cyan]{border}[/cyan]")


def print_code_panel(
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
