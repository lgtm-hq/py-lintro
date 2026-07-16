"""Display helpers for Lintro console output.

Contains ASCII art display functions and styling constants.
"""

import sys
from collections.abc import Callable

from loguru import logger

from lintro.enums.action import Action
from lintro.utils.formatting import read_ascii_art

# Constants for border lengths
BORDER_LENGTH: int = 50
INFO_BORDER_LENGTH: int = 40


def print_ascii_art(
    console_output_func: Callable[..., None],
    issue_count: int,
) -> None:
    """Print ASCII art based on the issue count.

    Only prints when stdout is an interactive TTY to avoid polluting
    CI logs, piped output, and report files.

    Args:
        console_output_func: Function to output text to console
        issue_count: The number of issues (remaining, fixed, or total)
    """
    if not sys.stdout.isatty():
        return
    try:
        if issue_count == 0:
            ascii_art = read_ascii_art(filename="success.txt")
        else:
            ascii_art = read_ascii_art(filename="fail.txt")

        if ascii_art:
            art_text: str = "\n".join(ascii_art)
            console_output_func(text=art_text)
    except (OSError, ValueError, TypeError) as e:
        logger.debug(f"Could not load ASCII art: {e}")


def print_final_status(
    console_output_func: Callable[..., None],
    action: Action,
    total_issues: int,
) -> None:
    """Print the final status for the run.

    Args:
        console_output_func: Function to output text to console
        action: Action: The Action enum value representing the action being performed.
        total_issues: int: The total number of issues found.
    """
    try:
        import click

        if action == Action.FIX:
            # Format operations: show success regardless of fixes made
            if total_issues == 0:
                final_msg: str = "✓ No issues found."
            else:
                final_msg = f"✓ Fixed {total_issues} issues."
            console_output_func(text=click.style(final_msg, fg="green", bold=True))
        else:  # check
            # Check operations: show failure if issues found
            if total_issues == 0:
                final_msg = "✓ No issues found."
                console_output_func(text=click.style(final_msg, fg="green", bold=True))
            else:
                final_msg = f"✗ Found {total_issues} issues."
                console_output_func(text=click.style(final_msg, fg="red", bold=True))

        console_output_func(text="")
    except ImportError:
        # Fallback if click not available
        if action == Action.FIX:
            if total_issues == 0:
                final_msg = "✓ No issues found."
            else:
                final_msg = f"✓ Fixed {total_issues} issues."
            console_output_func(text=f"\033[92m{final_msg}\033[0m")  # green
        else:  # check
            if total_issues == 0:
                final_msg = "✓ No issues found."
                console_output_func(text=f"\033[92m{final_msg}\033[0m")  # green
            else:
                final_msg = f"✗ Found {total_issues} issues."
                console_output_func(text=f"\033[91m{final_msg}\033[0m")  # red

        console_output_func(text="")


def print_final_status_format(
    console_output_func: Callable[..., None],
    total_fixed: int,
    total_remaining: int,
) -> None:
    """Print the final status for format operations.

    Args:
        console_output_func: Function to output text to console
        total_fixed: int: The total number of issues fixed.
        total_remaining: int: The total number of remaining issues.
    """
    try:
        import click

        if total_remaining == 0:
            if total_fixed == 0:
                final_msg: str = "✓ No issues found."
            else:
                final_msg = f"✓ {total_fixed} fixed"
            console_output_func(text=click.style(final_msg, fg="green", bold=True))
        else:
            if total_fixed > 0:
                fixed_msg: str = f"✓ {total_fixed} fixed"
                console_output_func(text=click.style(fixed_msg, fg="green", bold=True))
            remaining_msg: str = f"✗ {total_remaining} remaining"
            console_output_func(text=click.style(remaining_msg, fg="red", bold=True))

        console_output_func(text="")
    except ImportError:
        # Fallback if click not available
        if total_remaining == 0:
            if total_fixed == 0:
                final_msg = "✓ No issues found."
            else:
                final_msg = f"✓ {total_fixed} fixed"
            console_output_func(text=f"\033[92m{final_msg}\033[0m")  # green
        else:
            if total_fixed > 0:
                fixed_msg = f"✓ {total_fixed} fixed"
                console_output_func(text=f"\033[92m{fixed_msg}\033[0m")  # green
            remaining_msg = f"✗ {total_remaining} remaining"
            console_output_func(text=f"\033[91m{remaining_msg}\033[0m")  # red

        console_output_func(text="")
