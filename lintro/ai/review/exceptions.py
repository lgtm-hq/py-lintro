"""Review-specific exceptions for the AI diff review command."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from lintro.ai.exceptions import AIError
from lintro.ai.review.enums.review_context_error_code import ReviewContextErrorCode

if TYPE_CHECKING:
    from lintro.ai.review.errors_taxonomy import ReviewErrorKind


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
        error_kind: Canonical classification of the underlying cause, resolved
            provider-aware at the raise site. ``None`` when not yet classified.
    """

    message: str
    chunk_index: int | None = None
    total_chunks: int | None = None
    step: str | None = None
    completed_chunks: int = 0
    cause_message: str = ""
    error_kind: ReviewErrorKind | None = None

    def __post_init__(self) -> None:
        """Initialize the base exception args from the message field."""
        super().__init__(self.message)

    def __str__(self) -> str:
        """Return the human-readable failure message."""
        return self.message


class ReviewPreparationError(AIError):
    """Deterministic review preparation could not produce a runnable review.

    Raised by :func:`lintro.ai.review.preparation.prepare_review` when the
    request and the resolved configuration together describe a review that
    would review nothing — today, ``review.custom_agents: only`` with no valid
    agent files. Adapters translate it into their own surface error (a Click
    usage error for the CLI, a structured envelope for MCP) rather than the
    shared layer owning either policy.
    """


#: Where :func:`annotate_completed_chunks` keeps the count on an exception
#: that has no field of its own.
COMPLETED_CHUNKS_ATTR = "lintro_completed_chunks"


def annotate_completed_chunks(error: BaseException, *, completed: int) -> None:
    """Record on *error* how many chunks the run had completed when it left.

    A CI wrapper deciding whether to restart the review on another lane
    (ADR-0011 D) must not infer that from checkpoint files, which are
    best-effort and may have failed to write. The run boundary stamps the
    count on whatever exception escapes, raw provider errors included.

    Args:
        error: The exception leaving the run.
        completed: Chunks whose partial the run had collected.
    """
    setattr(error, COMPLETED_CHUNKS_ATTR, max(int(completed), 0))


def completed_chunks_of(error: BaseException) -> int | None:
    """Return how many chunks had completed when *error* left the run.

    Args:
        error: The exception the run raised.

    Returns:
        The larger of the execution error's own count and the run-boundary
        annotation, or ``None`` when neither is present: the count is unknown
        and a wrapper must not restart the review elsewhere.
    """
    counts: list[int] = []
    if isinstance(error, ReviewExecutionError):
        counts.append(max(int(error.completed_chunks), 0))
    annotated = getattr(error, COMPLETED_CHUNKS_ATTR, None)
    if isinstance(annotated, int) and not isinstance(annotated, bool):
        counts.append(annotated)
    return max(counts) if counts else None
