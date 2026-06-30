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
