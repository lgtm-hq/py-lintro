"""Shared onboarding helpers for install, setup, init, and first-run flows."""

from __future__ import annotations

import sys

from rich.console import Console


def is_interactive_tty() -> bool:
    """Return True when stdin is a TTY suitable for prompts."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def print_install_next_steps(console: Console, *, include_init: bool = False) -> None:
    """Print post-install guidance."""
    console.print()
    console.print("  [bold]Next steps:[/bold]")
    if include_init:
        console.print("    lintro init         [dim]Configure this project[/dim]")
    console.print("    lintro doctor       [dim]Verify tool health[/dim]")
    console.print("    lintro check .      [dim]Run checks[/dim]")
    console.print()


def print_first_run_guidance(console: Console) -> None:
    """Print guidance when no tools are available to run."""
    console.print()
    console.print("  [yellow]No tools available to run.[/yellow]")
    console.print()
    console.print("  [bold]Get started:[/bold]")
    console.print(
        "    lintro doctor" "                      [dim]See available tools[/dim]",
    )
    console.print(
        "    lintro init" "                        [dim]Configure this project[/dim]",
    )
    console.print(
        "    lintro install --profile recommended" "   [dim]Install common tools[/dim]",
    )
    console.print(
        "    lintro install ruff mypy" "           [dim]Install specific tools[/dim]",
    )
    console.print()
    console.print(
        "  [dim]Or install tools yourself; lintro detects them on PATH.[/dim]",
    )
    console.print()
