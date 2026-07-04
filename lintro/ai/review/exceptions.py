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
        code: ReviewContextErrorCode | str,
    ) -> None:
        """Initialize a review context error.

        Args:
            message: Human-readable error detail.
            code: Stable machine-readable error code.

        Raises:
            TypeError: If ``code`` is not an enum member or valid code string.
            ValueError: If ``code`` is a string that is not a valid enum value.
        """
        super().__init__(message)
        if isinstance(code, ReviewContextErrorCode):
            self.code = code
        elif isinstance(code, str):
            try:
                self.code = ReviewContextErrorCode(code)
            except ValueError as exc:
                msg = f"invalid review context error code: {code!r}"
                raise ValueError(msg) from exc
        else:
            msg = "code must be a ReviewContextErrorCode or valid error code string"
            raise TypeError(msg)


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

    def __post_init__(self) -> None:
        """Initialize the base exception args from the message field."""
        super().__init__(self.message)

    def __str__(self) -> str:
        """Return the human-readable failure message."""
        return self.message
