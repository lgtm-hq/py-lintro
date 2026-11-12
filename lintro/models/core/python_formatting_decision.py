"""Immutable execution policy for a run selecting Ruff and Black."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lintro.models.core.tool_result import ToolResult


@dataclass(frozen=True)
class PythonFormattingDecision:
    """One ownership decision applied throughout execution and reporting.

    Attributes:
        owner: Confirmed formatting owner, or None when absent or unconfirmed.
        reason: Capability diagnosis supporting the decision.
        ruff_format: Whether Ruff's write stage is enabled.
        ruff_format_check: Whether Ruff's formatting check is enabled.
        run_black: Whether Black may execute in this invocation.
        black_result: Frozen failure or unavailable result when Black cannot run.
        text: Human-readable rendering of this recorded decision.
    """

    owner: str | None
    reason: str
    ruff_format: bool
    ruff_format_check: bool
    run_black: bool
    black_result: ToolResult | None
    text: str
