"""Untyped legacy plugin fixtures, independent of the frozen result constructor."""

from types import SimpleNamespace
from typing import Any, cast

from lintro.models.core.tool_outcome import ToolOutcome, outcome_from_result
from lintro.models.core.tool_result import ToolResult


class _LegacyResult(SimpleNamespace):
    """Simulate an external plugin that has not adopted ToolResult validation."""

    @property
    def outcome(self) -> ToolOutcome:
        """Validate legacy evidence when a consumer reads it.

        Returns:
            The adapted execution and accounting state.
        """
        return outcome_from_result(cast(ToolResult, self))

    def validate_outcome(self) -> None:
        """Reject legacy evidence through the production validator."""
        ToolResult.validate_outcome(cast(ToolResult, self))


def legacy_result(**fields: Any) -> ToolResult:
    """Supply legacy plugin fields without bypassing frozen ToolResult validation.

    Args:
        **fields: Legacy fields, including intentionally malformed evidence.

    Returns:
        A duck-typed plugin result for boundary tests.
    """
    return cast(ToolResult, _LegacyResult(**(vars(ToolResult()) | fields)))
