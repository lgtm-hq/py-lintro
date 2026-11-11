"""Immutable execution policy for a run selecting Ruff and Black."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PythonFormattingDecision:
    """One ownership decision applied throughout execution and reporting.

    Attributes:
        owner: Confirmed formatting owner, or None when absent or unconfirmed.
        reason: Capability diagnosis supporting the decision.
        ruff_format: Whether Ruff's write stage is enabled.
        ruff_format_check: Whether Ruff's formatting check is enabled.
        run_black: Whether Black may execute in this invocation.
        black_unavailable: Whether Black was confirmed absent or unrunnable.
        text: Human-readable rendering of this recorded decision.
    """

    owner: str | None
    reason: str
    ruff_format: bool
    ruff_format_check: bool
    run_black: bool
    black_unavailable: bool
    text: str
