"""File processing utilities for tools that process files one at a time.

This module provides dataclasses and utilities for tools that need to process
files individually (rather than in batch mode). It extracts the common pattern
of iterating through files, collecting results, and building output.

Example:
    >>> from lintro.plugins.file_processor import (
    ...     AggregatedResult,
    ...     FileProcessingResult,
    ... )
    >>>
    >>> def process_file(path: str) -> FileProcessingResult:
    ...     # Process the file
    ...     return FileProcessingResult(success=True, output="", issues=[])
    >>>
    >>> result = AggregatedResult()
    >>> for file_path in files:
    ...     file_result = process_file(file_path)
    ...     result.add_file_result(file_path, file_result)
    >>> output = result.build_output()
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lintro.parsers.base_issue import BaseIssue


@dataclass
class FileProcessingResult:
    """Result from processing a single file.

    Attributes:
        success: Whether the file was processed successfully (exit code 0).
        output: Raw output from the tool for this file.
        issues: List of issues found in this file.
        skipped: Whether the file was skipped (e.g., due to timeout).
        error: Error message if processing failed.
    """

    success: bool
    output: str
    issues: Sequence[BaseIssue]
    skipped: bool = False
    error: str | None = None


@dataclass
class FileFixResult:
    """Result from processing a single file in fix mode.

    Bundles the standard FileProcessingResult with fix-specific metrics
    so tools can report both pre-fix detection and post-fix remaining
    issues to the two-table display pipeline.

    Attributes:
        file_result: Standard per-file processing result (carries the
            remaining issues in `file_result.issues`).
        initial_count: Number of issues detected before the fix ran.
        fixed_count: Number of issues the fix resolved.
        initial_issues: The pre-fix issues, preserved for display.
    """

    file_result: FileProcessingResult
    initial_count: int
    fixed_count: int
    initial_issues: Sequence[BaseIssue]

    @property
    def remaining_count(self) -> int:
        """Number of issues still present after the fix ran.

        Derived from ``initial_count`` and ``fixed_count`` so the
        ``initial == fixed + remaining`` invariant is enforced at the
        source.

        Returns:
            Issues remaining after the fix attempt.
        """
        return max(0, self.initial_count - self.fixed_count)

    @property
    def remaining_issues(self) -> Sequence[BaseIssue]:
        """Issues still present after the fix ran.

        Exposes ``file_result.issues`` so aggregators can collect the
        actual issue objects (rather than just a count). This covers
        skipped/errored files, whose issues would otherwise be dropped
        by ``AggregatedResult``.

        Returns:
            Issues remaining after the fix attempt.
        """
        return self.file_result.issues


@dataclass
class AggregatedResult:
    """Aggregated results from processing multiple files.

    This class collects results from processing multiple files and provides
    methods to build the final output and determine overall success.

    Attributes:
        all_success: Whether all files were processed successfully.
        all_issues: Combined list of all issues from all files.
        all_outputs: List of non-empty outputs from files with issues.
        skipped_files: List of file paths that were skipped.
        execution_failures: Count of files that failed to process.
        total_issues: Total count of issues across all files.
    """

    all_success: bool = True
    all_issues: list[BaseIssue] = field(default_factory=list)
    all_outputs: list[str] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)
    execution_failures: int = 0
    total_issues: int = 0

    def add_file_result(self, file_path: str, result: FileProcessingResult) -> None:
        """Add a single file's result to the aggregate.

        Args:
            file_path: Path to the file that was processed.
            result: The processing result for this file.
        """
        if result.skipped:
            self.skipped_files.append(file_path)
            self.all_success = False
            self.execution_failures += 1
            return

        if result.error:
            self.all_outputs.append(f"Error processing {file_path}: {result.error}")
            self.all_success = False
            self.execution_failures += 1
            return

        issues_count = len(result.issues)
        self.total_issues += issues_count

        if not result.success:
            self.all_success = False

        if (not result.success or result.issues) and result.output:
            self.all_outputs.append(result.output)

        if result.issues:
            self.all_issues.extend(result.issues)

    def build_output(self, *, timeout: int | None = None) -> str | None:
        """Build the final output string.

        Args:
            timeout: Timeout value to include in failure messages.

        Returns:
            Combined output string, or None if no output.
        """
        output = "\n".join(self.all_outputs) if self.all_outputs else ""

        if self.execution_failures > 0:
            if output:
                output += "\n\n"
            if self.skipped_files:
                output += (
                    f"Skipped/failed {self.execution_failures} file(s) due to "
                    f"execution failures (including timeouts)"
                )
                if timeout is not None:
                    output += f" (timeout: {timeout}s):"
                else:
                    output += ":"
                for file in self.skipped_files:
                    output += f"\n  - {file}"
            else:
                output += (
                    f"Failed to process {self.execution_failures} file(s) "
                    "due to execution errors"
                )

        return output if output.strip() else None


__all__ = [
    "AggregatedResult",
    "FileFixResult",
    "FileProcessingResult",
]
