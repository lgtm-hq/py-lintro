"""Fix validation rendering for terminal output."""

from __future__ import annotations

import io

from rich.console import Console
from rich.markup import escape

from lintro.ai.validation import ValidationResult
from lintro.utils.console.constants import BORDER_LENGTH


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
        parts.append(f"[green]{result.verified} resolved[/green]")
    if result.unverified:
        parts.append(f"[yellow]{result.unverified} still present[/yellow]")
    sep = " \u00b7 "
    console.print(f"  [bold]Fix validation:[/bold] {sep.join(parts)}")

    for detail in result.details:
        console.print(f"    [yellow]![/yellow] {escape(detail)}")

    return buf.getvalue()


def render_validation(result: ValidationResult) -> str:
    """Render validation as terminal-formatted output.

    Args:
        result: Validation result to render.

    Returns:
        Formatted validation string for terminal display.
    """
    return render_validation_terminal(result)
