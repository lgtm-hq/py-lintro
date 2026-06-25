"""Review-specific exceptions for the AI diff review command."""

from __future__ import annotations

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
