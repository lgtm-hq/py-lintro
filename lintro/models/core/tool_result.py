"""Models for core tool execution results.

This module defines the canonical result object returned by all tools. It
supports both check and fix flows and includes standardized fields to report
fixed vs remaining counts for fix-capable tools.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lintro.parsers.base_issue import BaseIssue


@dataclass
class ToolResult:
    """Result of running a tool.

    For check operations:
        - ``issues_count`` represents the number of issues found.

    For fix/format operations:
        - ``initial_issues_count`` is the number of issues detected before fixes
        - ``fixed_issues_count`` is the number of issues the tool auto-fixed
        - ``remaining_issues_count`` is the number of issues still remaining
        - ``issues_count`` should mirror ``remaining_issues_count`` for
          backward compatibility in format-mode summaries

    The ``issues`` field can contain parsed issue objects (tool-specific) to
    support unified table formatting.
    """

    name: str = field(default="")
    success: bool = field(default=False)
    output: str | None = field(default=None)
    issues_count: int = field(default=0)
    formatted_output: str | None = field(default=None)
    issues: Sequence[BaseIssue] | None = field(default=None)

    # Optional standardized counts for fix-capable tools
    initial_issues_count: int | None = field(default=None)
    fixed_issues_count: int | None = field(default=None)
    remaining_issues_count: int | None = field(default=None)

    # Optional pytest-specific summary data for display
    pytest_summary: dict[str, Any] | None = field(default=None)

    # Optional AI-generated metadata (explanations, fix suggestions)
    ai_metadata: dict[str, Any] | None = field(default=None)

    # Working directory used during tool execution (for resolving relative
    # issue file paths in AI fix generation)
    cwd: str | None = field(default=None)

    # Skip tracking for tools that didn't execute
    skipped: bool = field(default=False)
    skip_reason: str | None = field(default=None)

    def __post_init__(self) -> None:
        """Validate that the issue counts and skip state are consistent.

        Raises:
            ValueError: If issue counts are inconsistent or skip state is invalid.
        """
        # Skipped tools are not failures — they just didn't run
        if self.skipped:
            self.success = True
            if not self.skip_reason:
                raise ValueError(
                    "skip_reason is required when skipped=True",
                )

        if self.skip_reason and not self.skipped:
            raise ValueError(
                "skip_reason can only be set when skipped=True",
            )

        if (
            self.initial_issues_count is not None
            and self.fixed_issues_count is not None
            and self.remaining_issues_count is not None
            and self.initial_issues_count
            != self.fixed_issues_count + self.remaining_issues_count
        ):
            raise ValueError(
                f"Inconsistent issue counts: "
                f"initial={self.initial_issues_count}, "
                f"fixed={self.fixed_issues_count}, "
                f"remaining={self.remaining_issues_count}. "
                f"Expected: initial = fixed + remaining",
            )
