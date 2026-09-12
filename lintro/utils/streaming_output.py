"""Streaming output handler for memory-efficient result processing.

This module provides functionality to process and output tool results as they
arrive, instead of buffering all results in memory before output.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TextIO

from loguru import logger

if TYPE_CHECKING:
    from lintro.enums.action import Action
    from lintro.models.core.tool_result import ToolResult


@dataclass
class StreamingResultHandler:
    """Handle tool results as they arrive, without buffering all in memory.

    This handler supports streaming output in various formats:
    - Console: Print formatted results immediately
    - JSONL: Write one JSON object per line
    - File: Write to file as results arrive

    Attributes:
        output_format: Output format (grid, json, jsonl, etc.).
        action: The action being performed.
        output_file: Optional file path for output.
    """

    output_format: str
    action: Action
    output_file: str | None = None
    _file_handle: TextIO | None = field(default=None, init=False)
    _totals: dict[str, int] = field(default_factory=dict, init=False)
    _results_buffer: list[ToolResult] = field(default_factory=list, init=False)
    _first_jsonl: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        """Initialize totals dictionary."""
        self._totals = {
            "issues": 0,
            "fixed": 0,
            "remaining": 0,
            "tools_run": 0,
            "tools_failed": 0,
        }

    def __enter__(self) -> StreamingResultHandler:
        """Open file handle if output file is specified.

        Returns:
            Self for context manager protocol.
        """
        if self.output_file:
            try:
                self._file_handle = open(self.output_file, "w", encoding="utf-8")
                if self.output_format.lower() == "jsonl":
                    # JSONL format starts immediately
                    pass
                elif self.output_format.lower() == "json":
                    # For JSON array format, write opening bracket
                    self._file_handle.write("[\n")
            except OSError as e:
                logger.warning(f"Could not open output file {self.output_file}: {e}")
                self._file_handle = None
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Close file handle.

        Args:
            exc_type: Exception type if an exception was raised.
            exc_val: Exception value if an exception was raised.
            exc_tb: Exception traceback if an exception was raised.
        """
        if self._file_handle:
            try:
                if self.output_format.lower() == "json":
                    # Close JSON array
                    self._file_handle.write("\n]")
                self._file_handle.close()
            except OSError as e:
                logger.warning(f"Error closing output file: {e}")

    def handle_result(
        self,
        result: ToolResult,
        console_output_func: Callable[[str], None] | None = None,
        raw_output: bool = False,
    ) -> None:
        """Process a single tool result immediately.

        Args:
            result: The tool result to process.
            console_output_func: Optional function to print to console.
            raw_output: Whether to use raw output instead of formatted.
        """
        from lintro.enums.action import Action

        # Update totals
        self._totals["tools_run"] += 1
        self._totals["issues"] += result.issues_count

        if self.action == Action.FIX:
            fixed = getattr(result, "fixed_issues_count", None)
            remaining = getattr(result, "remaining_issues_count", None)
            if fixed is not None:
                self._totals["fixed"] += fixed
            if remaining is not None:
                self._totals["remaining"] += remaining

        if not result.success:
            self._totals["tools_failed"] += 1

        # Buffer for summary/JSON output
        self._results_buffer.append(result)

        # Stream output based on format
        if self.output_format.lower() == "jsonl":
            self._write_jsonl(result)
        elif self.output_format.lower() == "json":
            self._write_json_item(result)

    def _write_jsonl(self, result: ToolResult) -> None:
        """Write result as JSON Lines (one object per line).

        Args:
            result: The tool result to write.
        """
        data = self._result_to_dict(result)
        json_line = json.dumps(data)

        if self._file_handle:
            self._file_handle.write(json_line + "\n")
            self._file_handle.flush()

    def _write_json_item(self, result: ToolResult) -> None:
        """Write result as JSON array item.

        Args:
            result: The tool result to write.
        """
        if self._file_handle:
            data = self._result_to_dict(result)
            if not self._first_jsonl:
                self._file_handle.write(",\n")
            json_str = json.dumps(data, indent=2)
            # Indent each line for pretty printing in array
            indented = "\n".join("  " + line for line in json_str.split("\n"))
            self._file_handle.write(indented)
            self._first_jsonl = False
            self._file_handle.flush()

    def _result_to_dict(self, result: ToolResult) -> dict[str, Any]:
        """Convert ToolResult to dictionary for JSON serialization.

        Args:
            result: The tool result to convert.

        Returns:
            Dictionary representation of the result.
        """
        data: dict[str, Any] = {
            "tool": result.name,
            "success": result.success,
            "issues_count": result.issues_count,
        }

        if result.output:
            data["output"] = result.output

        if result.initial_issues_count is not None:
            data["initial_issues_count"] = result.initial_issues_count
        if result.fixed_issues_count is not None:
            data["fixed_issues_count"] = result.fixed_issues_count
        if result.remaining_issues_count is not None:
            data["remaining_issues_count"] = result.remaining_issues_count
        if result.residual_unknown:
            # Neither count is present above: the verify pass never measured
            # one (#1743). Flag it so a reader does not infer zero.
            data["residual_unknown"] = True
            data["residual_unknown_reason"] = result.residual_unknown_reason

        # Include issues if available
        if result.issues:
            data["issues"] = [
                {
                    "file": issue.file,
                    "line": issue.line,
                    "column": issue.column,
                    "message": issue.message,
                }
                for issue in result.issues
            ]

        return data

    def get_totals(self) -> dict[str, int]:
        """Get accumulated totals.

        Returns:
            Dictionary with total counts.
        """
        return self._totals.copy()

    def get_results(self) -> list[ToolResult]:
        """Get buffered results for final processing.

        Returns:
            List of all processed results.
        """
        return self._results_buffer.copy()

    def get_exit_code(self) -> int:
        """Calculate exit code based on results.

        Returns:
            0 for success, 1 for failures.
        """
        from lintro.enums.action import Action

        # Any tool failure means exit code 1
        if self._totals["tools_failed"] > 0:
            return 1

        # Check for issues based on action
        if self.action == Action.FIX:
            if self._totals["remaining"] > 0:
                return 1
        else:  # check
            if self._totals["issues"] > 0:
                return 1

        return 0


def create_streaming_handler(
    output_format: str,
    action: Action,
    output_file: str | None = None,
) -> StreamingResultHandler:
    """Create a streaming result handler.

    Args:
        output_format: Output format (grid, json, jsonl, etc.).
        action: The action being performed.
        output_file: Optional file path for output.

    Returns:
        Configured StreamingResultHandler instance.
    """
    return StreamingResultHandler(
        output_format=output_format,
        action=action,
        output_file=output_file,
    )
