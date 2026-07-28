"""Models for core tool execution results.

This module defines the canonical result object returned by all tools. It
supports both check and fix flows and includes standardized fields to report
fixed vs remaining counts for fix-capable tools.
"""

from __future__ import annotations

import functools
import warnings
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lintro.parsers.base_issue import BaseIssue

_AI_METADATA_DEPRECATION = (
    "ToolResult.ai_metadata is deprecated and will be removed in a future "
    "release; use ToolResult.metadata instead. The field is not AI-specific "
    "(osv-scanner stores suppression data in it)."
)


def _warn_ai_metadata(*, stacklevel: int) -> None:
    """Emit the ``ai_metadata`` deprecation warning.

    Args:
        stacklevel: Stack level to attribute the warning to, so the warning
            points at the caller rather than at this module.
    """
    warnings.warn(
        _AI_METADATA_DEPRECATION,
        DeprecationWarning,
        stacklevel=stacklevel,
    )


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

    Convention: for FIX action, remaining issues occupy the tail of the
    ``issues`` list. Tools append all detected issues in order, so the
    last ``remaining_issues_count`` entries are the ones still unfixed.
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

    # Pre-fix issues detected before applying fixes (for displaying what was fixed)
    initial_issues: Sequence[BaseIssue] | None = field(default=None)

    # Optional pytest-specific summary data for display
    pytest_summary: dict[str, Any] | None = field(default=None)

    # Optional tool metadata. Most keys are AI-generated (explanations, fix
    # suggestions), but the field is not AI-specific: osv-scanner stores its
    # suppression classifications here with the AI layer fully disabled.
    # Expected keys (all optional):
    #   "fix_suggestions": list[AIFixSuggestionPayload]  (serialized)
    #   "fixed_count": int
    #   "verified_count": int
    #   "unverified_count": int
    #   "telemetry": dict with api_calls, tokens, cost, latency
    #   "suppressions": list[dict]  (osv-scanner, non-AI)
    # AI keys are built incrementally via helpers in lintro.ai.metadata.
    metadata: dict[str, Any] | None = field(default=None)

    # Working directory used during tool execution (for resolving relative
    # issue file paths in AI fix generation)
    cwd: str | None = field(default=None)

    # Skip tracking for tools that didn't execute
    skipped: bool = field(default=False)
    skip_reason: str | None = field(default=None)

    # Execution-timeout tracking. ``True`` when the tool's subprocess exceeded
    # its deadline and was killed. A timeout is an *execution failure*, not a
    # lint finding: it never contributes to ``issues_count``. Consumers use
    # this flag (surfaced as ``timed_out`` in the JSON report) to tell an
    # infrastructure flake apart from a genuine finding without regex-matching
    # the human-readable ``output`` string.
    timed_out: bool = field(default=False)

    # Parser failures (items that could not be parsed from tool output).
    # Omitted from JSON output unless the tool sets this explicitly.
    parse_failures_count: int | None = field(default=None)

    @property
    def ai_metadata(self) -> dict[str, Any] | None:
        """Deprecated alias for :attr:`metadata`.

        Defined without a type annotation so :func:`dataclasses.dataclass`
        does not treat it as a second field: ``dataclasses.fields`` still
        reports only ``metadata``, and both names are views onto the same
        dict.

        Returns:
            The value of :attr:`metadata`.
        """
        _warn_ai_metadata(stacklevel=3)
        return self.metadata

    @ai_metadata.setter
    def ai_metadata(self, value: dict[str, Any] | None) -> None:
        """Deprecated alias setter that writes through to :attr:`metadata`.

        Args:
            value: Metadata dict to store on :attr:`metadata`.
        """
        _warn_ai_metadata(stacklevel=3)
        self.metadata = value

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


_dataclass_init = ToolResult.__init__


@functools.wraps(_dataclass_init)
def _tool_result_init(self: ToolResult, *args: Any, **kwargs: Any) -> None:
    """Accept the deprecated ``ai_metadata`` keyword during construction.

    ``ai_metadata`` is an alias property rather than a dataclass field, so the
    generated ``__init__`` does not know about it. This wrapper folds the
    deprecated keyword into ``metadata`` before delegating, which keeps
    ``ToolResult(ai_metadata=...)``, ``dataclasses.replace`` and
    ``__post_init__`` all working while ``dataclasses.fields`` still reports a
    single ``metadata`` field.

    Args:
        self: The instance being initialized.
        *args: Positional arguments for the generated ``__init__``.
        **kwargs: Keyword arguments, optionally including ``ai_metadata``.

    Raises:
        TypeError: If both ``metadata`` and ``ai_metadata`` are supplied.
    """
    if "ai_metadata" in kwargs:
        if "metadata" in kwargs:
            raise TypeError(
                "ToolResult() got both 'metadata' and the deprecated "
                "'ai_metadata'; pass only 'metadata'",
            )
        alias_value = kwargs.pop("ai_metadata")
        _warn_ai_metadata(stacklevel=2)
        kwargs["metadata"] = alias_value
    _dataclass_init(self, *args, **kwargs)


ToolResult.__init__ = _tool_result_init  # type: ignore[method-assign]
