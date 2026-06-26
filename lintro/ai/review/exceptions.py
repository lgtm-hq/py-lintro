"""Review-specific exceptions for the AI diff review command."""

from __future__ import annotations

from dataclasses import dataclass

from lintro.ai.exceptions import AIError
from lintro.ai.review.enums.review_context_error_code import ReviewContextErrorCode


class ReviewContextError(AIError):
    """Error collecting git diff context for review."""

    def __init__(
        self,
        message: str,
        *,
        code: ReviewContextErrorCode,
    ) -> None:
        """Initialize a review context error.

        Args:
            message: Human-readable error detail.
            code: Stable machine-readable error code.
        """
        super().__init__(message)
        self.code = code


@dataclass
class ReviewExecutionError(AIError):
    """Review failed mid-run after one or more chunks completed.

    Attributes:
        message: Human-readable failure summary.
        chunk_index: Zero-based index of the chunk that failed, if known.
        total_chunks: Total chunks planned for the review.
        step: Sub-step within the chunk (e.g. "reviewing").
        completed_chunks: Number of chunks successfully reviewed before failure.
        cause_message: Original provider or parser error text.
    """

    message: str
    chunk_index: int | None = None
    total_chunks: int | None = None
    step: str | None = None
    completed_chunks: int = 0
    cause_message: str = ""

    def __str__(self) -> str:
        """Return the human-readable failure message."""
        return self.message
