"""Thread-safe console logger for formatted output display.

This module provides the ThreadSafeConsoleLogger class for console output
with thread-safe message tracking for parallel execution.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import click
from loguru import logger

from lintro.enums.action import Action, normalize_action
from lintro.enums.severity_level import SeverityLevel
from lintro.enums.tool_name import ToolName
from lintro.utils.console.constants import (
    BORDER_LENGTH,
    RE_CANNOT_AUTOFIX,
    RE_REMAINING_OR_CANNOT,
    get_tool_emoji,
)
from lintro.utils.display_helpers import (
    print_ascii_art,
    print_final_status,
    print_final_status_format,
)


def _get_ai_count(result: object, key: str) -> int:
    """Get an integer AI metadata count from a result object."""
    ai_metadata = getattr(result, "ai_metadata", None)
    if not isinstance(ai_metadata, dict):
        return 0
    value = ai_metadata.get(key, 0)
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


class ThreadSafeConsoleLogger:
    """Thread-safe logger for console output formatting and display.

    This class handles both console output and message tracking with proper
    thread synchronization for parallel tool execution.
    """

    def __init__(self, run_dir: Path | None = None) -> None:
        """Initialize the ThreadSafeConsoleLogger.

        Args:
            run_dir: Optional run directory path for output location display.
        """
        self.run_dir = run_dir
        self._messages: list[str] = []
        self._lock = threading.Lock()

    def console_output(self, text: str, color: str | None = None) -> None:
        """Display text on console and track for console.log.

        Thread-safe: Uses lock when appending to message list.

        Args:
            text: Text to display.
            color: Optional color for the text.
        """
        if color:
            click.echo(click.style(text, fg=color))
        else:
            click.echo(text)

        # Track for console.log (thread-safe)
        with self._lock:
            self._messages.append(text)

    def info(self, message: str, **kwargs: Any) -> None:
        """Log an info message to the console.

        Args:
            message: The message to log.
            **kwargs: Additional keyword arguments for logger formatting.
        """
        self.console_output(message)
        logger.info(message, **kwargs)

    def info_blue(self, message: str, **kwargs: Any) -> None:
        """Log an info message to the console in blue color.

        Args:
            message: The message to log.
            **kwargs: Additional keyword arguments for formatting.
        """
        self.console_output(message, color="cyan")
        logger.info(message, **kwargs)

    def debug(self, message: str) -> None:
        """Log a debug message (only shown when debug logging is enabled).

        Args:
            message: Message to log.
        """
        logger.debug(message)

    def warning(self, message: str, **kwargs: Any) -> None:
        """Log a warning message to the console.

        Args:
            message: The message to log.
            **kwargs: Additional keyword arguments for logger formatting.
        """
        warning_text = f"WARNING: {message}"
        self.console_output(warning_text, color="yellow")
        logger.warning(message, **kwargs)

    def error(self, message: str, **kwargs: Any) -> None:
        """Log an error message to the console.

        Args:
            message: The message to log.
            **kwargs: Additional keyword arguments for logger formatting.
        """
        error_text = f"ERROR: {message}"
        click.echo(click.style(error_text, fg="red", bold=True))
        with self._lock:
            self._messages.append(error_text)
        logger.error(message, **kwargs)

    def success(self, message: str, **kwargs: Any) -> None:
        """Log a success message to the console.

        Args:
            message: The message to log.
            **kwargs: Additional keyword arguments for logger formatting.
        """
        self.console_output(text=f"✅ {message}", color="green")
        logger.info(f"SUCCESS: {message}", **kwargs)

    def save_console_log(self, run_dir: str | Path | None = None) -> None:
        """Save tracked console messages to console.log.

        Thread-safe: Uses lock when reading message list.

        Args:
            run_dir: Directory to save the console log. If None, uses self.run_dir.
        """
        target_dir = Path(run_dir) if run_dir else self.run_dir
        if not target_dir:
            return

        console_log_path = target_dir / "console.log"
        try:
            with self._lock:
                messages = list(self._messages)

            with open(console_log_path, "w", encoding="utf-8") as f:
                for message in messages:
                    f.write(f"{message}\n")
            logger.debug(f"Saved console output to {console_log_path}")
        except OSError as e:
            logger.error(f"Failed to save console log to {console_log_path}: {e}")

    def print_execution_summary(
        self,
        action: Action,
        tool_results: Sequence[object],
    ) -> None:
        """Print the execution summary for all tools.

        Args:
            action: The action being performed.
            tool_results: The list of tool results.
        """
        # Add separation before Execution Summary
        self.console_output(text="")

        # Execution summary section
        summary_header: str = click.style("📋 EXECUTION SUMMARY", fg="cyan", bold=True)
        border_line: str = click.style("=" * 50, fg="cyan")

        self.console_output(text=summary_header)
        self.console_output(text=border_line)

        # Build summary table
        self._print_summary_table(action=action, tool_results=tool_results)

        # Totals line and ASCII art
        from lintro.utils.summary_tables import count_affected_files

        affected_files = count_affected_files(tool_results)

        if action == Action.FIX:
            # For format commands, track both fixed and remaining issues
            total_fixed: int = 0
            total_remaining: int = 0
            total_ai_applied: int = 0
            total_ai_verified: int = 0
            for result in tool_results:
                fixed_std = getattr(result, "fixed_issues_count", None)
                remaining_std = getattr(result, "remaining_issues_count", None)
                success = getattr(result, "success", True)
                total_ai_applied += _get_ai_count(result, "applied_count")
                total_ai_verified += _get_ai_count(result, "verified_count")

                if fixed_std is not None:
                    total_fixed += fixed_std
                else:
                    total_fixed += getattr(result, "issues_count", 0)

                if remaining_std is not None:
                    if isinstance(remaining_std, int):
                        total_remaining += remaining_std
                elif not success:
                    pass
                else:
                    output = getattr(result, "output", "")
                    if output and (
                        "remaining" in output.lower()
                        or "cannot be auto-fixed" in output.lower()
                    ):
                        remaining_match = RE_CANNOT_AUTOFIX.search(output)
                        if not remaining_match:
                            remaining_match = RE_REMAINING_OR_CANNOT.search(
                                output.lower(),
                            )
                        if remaining_match:
                            total_remaining += int(remaining_match.group(1))

            self._print_totals_table(
                action=action,
                total_fixed=total_fixed,
                total_remaining=total_remaining,
                affected_files=affected_files,
                total_ai_applied=total_ai_applied,
                total_ai_verified=total_ai_verified,
            )
            self._print_ascii_art(total_issues=total_remaining)
            logger.debug(
                f"{action} completed with native={total_fixed}, "
                f"ai_verified={total_ai_verified}, "
                f"{total_remaining} remaining",
            )
        else:
            total_issues: int = sum(
                (getattr(result, "issues_count", 0) for result in tool_results),
            )
            any_failed: bool = any(
                not getattr(result, "success", True) for result in tool_results
            )
            total_for_art: int = (
                total_issues if not any_failed else max(1, total_issues)
            )

            # Tally severity breakdown across all issues
            sev_errors = 0
            sev_warnings = 0
            sev_info = 0
            for result in tool_results:
                issues = getattr(result, "issues", None)
                if issues:
                    for issue in issues:
                        get_sev = getattr(issue, "get_severity", None)
                        if get_sev:
                            level = get_sev()
                            if level == SeverityLevel.ERROR:
                                sev_errors += 1
                            elif level == SeverityLevel.WARNING:
                                sev_warnings += 1
                            elif level == SeverityLevel.INFO:
                                sev_info += 1

            self._print_totals_table(
                action=action,
                total_issues=total_issues,
                affected_files=affected_files,
                severity_errors=sev_errors,
                severity_warnings=sev_warnings,
                severity_info=sev_info,
            )
            self._print_ascii_art(total_issues=total_for_art)
            logger.debug(
                f"{action} completed with {total_issues} total issues"
                + (" and failures" if any_failed else ""),
            )

    def _print_summary_table(
        self,
        action: Action | str,
        tool_results: Sequence[object],
    ) -> None:
        """Print the summary table for the run.

        Args:
            action: The action being performed.
            tool_results: The list of tool results.
        """
        from lintro.utils.summary_tables import print_summary_table

        action_enum = normalize_action(action)
        print_summary_table(
            console_output_func=self.console_output,
            action=action_enum,
            tool_results=tool_results,
        )

    def _print_totals_table(
        self,
        action: Action,
        total_issues: int = 0,
        total_fixed: int = 0,
        total_remaining: int = 0,
        affected_files: int = 0,
        severity_errors: int = 0,
        severity_warnings: int = 0,
        severity_info: int = 0,
        total_ai_applied: int = 0,
        total_ai_verified: int = 0,
    ) -> None:
        """Print the totals summary table for the run.

        Args:
            action: The action being performed.
            total_issues: Total number of issues found (CHECK/TEST mode).
            total_fixed: Total number of issues fixed (FIX mode).
            total_remaining: Total number of remaining issues (FIX mode).
            affected_files: Number of unique files with issues.
            severity_errors: Number of issues at ERROR severity.
            severity_warnings: Number of issues at WARNING severity.
            severity_info: Number of issues at INFO severity.
            total_ai_applied: Total number of AI-applied fixes (FIX mode).
            total_ai_verified: Total number of AI-verified fixes (FIX mode).
        """
        from lintro.utils.summary_tables import print_totals_table

        print_totals_table(
            console_output_func=self.console_output,
            action=action,
            total_issues=total_issues,
            total_fixed=total_fixed,
            total_remaining=total_remaining,
            affected_files=affected_files,
            severity_errors=severity_errors,
            severity_warnings=severity_warnings,
            severity_info=severity_info,
            total_ai_applied=total_ai_applied,
            total_ai_verified=total_ai_verified,
        )

    def _print_final_status(
        self,
        action: Action | str,
        total_issues: int,
    ) -> None:
        """Print the final status for the run.

        Args:
            action: The action being performed.
            total_issues: The total number of issues found.
        """
        action_enum = normalize_action(action)
        print_final_status(
            console_output_func=self.console_output,
            action=action_enum,
            total_issues=total_issues,
        )

    def _print_final_status_format(
        self,
        total_fixed: int,
        total_remaining: int,
    ) -> None:
        """Print the final status for format operations.

        Args:
            total_fixed: The total number of issues fixed.
            total_remaining: The total number of remaining issues.
        """
        print_final_status_format(
            console_output_func=self.console_output,
            total_fixed=total_fixed,
            total_remaining=total_remaining,
        )

    def _print_ascii_art(
        self,
        total_issues: int,
    ) -> None:
        """Print ASCII art based on the number of issues.

        Args:
            total_issues: The total number of issues found.
        """
        print_ascii_art(
            console_output_func=self.console_output,
            issue_count=total_issues,
        )

    def print_lintro_header(self) -> None:
        """Print the main Lintro header with output directory information."""
        if self.run_dir:
            header_msg: str = (
                f"[LINTRO] All output formats will be auto-generated in {self.run_dir}"
            )
            self.console_output(text=header_msg)
            self.console_output(text="")

    def print_tool_header(
        self,
        tool_name: str,
        action: str,
    ) -> None:
        """Print a formatted header for a tool execution.

        Args:
            tool_name: Name of the tool.
            action: The action being performed ("check" or "fmt").
        """
        emoji: str = get_tool_emoji(tool_name)
        border: str = "=" * BORDER_LENGTH
        header_text: str = (
            f"✨  Running {tool_name} ({action})    "
            f"{emoji} {emoji} {emoji} {emoji} {emoji}"
        )

        self.console_output(text=border)
        self.console_output(text=header_text)
        self.console_output(text=border)
        self.console_output(text="")

    def print_tool_result(
        self,
        tool_name: str,
        output: str,
        issues_count: int,
        raw_output_for_meta: str | None = None,
        action: Action | None = None,
        success: bool = True,
    ) -> None:
        """Print the result of a tool execution.

        Args:
            tool_name: Name of the tool.
            output: Tool output to display.
            issues_count: Number of issues found.
            raw_output_for_meta: Raw output for metadata parsing.
            action: Action being performed.
            success: Whether the tool execution was successful.
        """
        if output:
            self.console_output(text=output)
            self.console_output(text="")

        if raw_output_for_meta and action == Action.CHECK:
            self._print_metadata_messages(raw_output_for_meta)

        if action == Action.TEST and tool_name == ToolName.PYTEST.value:
            self._print_pytest_results(output, success)

    def _print_metadata_messages(self, raw_output: str) -> None:
        """Print metadata messages parsed from raw tool output.

        Args:
            raw_output: Raw tool output to parse for metadata.
        """
        output_lower = raw_output.lower()

        fixable_match = re.search(r"(\d+)\s*fixable", output_lower)
        if fixable_match:
            fixable_count = int(fixable_match.group(1))
            if fixable_count > 0:
                self.console_output(
                    text=f"Info: Found {fixable_count} auto-fixable issue(s)",
                )
            else:
                self.console_output(text="Info: No issues found")
            return

        if "cannot be auto-fixed" in output_lower:
            self.console_output(text="Info: Found issues that cannot be auto-fixed")
            return

        if "would reformat" in output_lower:
            self.console_output(text="Info: Files would be reformatted")
            return

        if "fixed" in output_lower:
            self.console_output(text="Info: Issues were fixed")
            return

        self.console_output(text="Info: No issues found")

    def _print_pytest_results(self, output: str, success: bool) -> None:
        """Print formatted pytest results.

        Args:
            output: Pytest output.
            success: Whether tests passed.
        """
        self.console_output(text="")
        self.console_output(text="📋 Test Results", color="cyan")
        self.console_output(text="=" * 50, color="cyan")

        if success:
            self.console_output(text="✅ All tests passed", color="green")
        else:
            self.console_output(text="❌ Some tests failed", color="red")

        if output:
            self.console_output(text="")
            self.console_output(text=output)

    def print_post_checks_header(
        self,
    ) -> None:
        """Print a distinct header separating the post-checks phase."""
        border_char: str = "━"
        border: str = border_char * BORDER_LENGTH
        title_styled: str = click.style(
            text="🚦  POST-CHECKS",
            fg="magenta",
            bold=True,
        )
        subtitle_styled: str = click.style(
            text=("Running optional follow-up checks after primary tools"),
            fg="magenta",
        )
        border_styled: str = click.style(text=border, fg="magenta")

        self.console_output(text=border_styled)
        self.console_output(text=title_styled)
        self.console_output(text=subtitle_styled)
        self.console_output(text=border_styled)
        self.console_output(text="")
