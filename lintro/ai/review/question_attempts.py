"""One retry for the per-PR question pass, and failure classification (#2813).

A failed pass is classified by kind. Only a call cut off at its turn limit
and an answer that was not JSON are retried, once, with the same prompt; a
turn-limited call is retried single-shot with no tools, the pattern chunks
use (#2735). A cost-cap stop and the SIGTERM timeout are the run's graceful
halt and propagate untouched on either attempt. Usage sums both attempts, so
the run's totals match what was charged.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING

from loguru import logger

from lintro.ai.cli_bounds import CallShape
from lintro.ai.exceptions import AITurnLimitError
from lintro.ai.review.enums.question_failure_kind import (
    RETRIED_KINDS,
    QuestionFailureKind,
)
from lintro.ai.review.interrupt import SIGTERM_TIMEOUT_MESSAGE
from lintro.ai.review.merge import ChunkReviewPartial
from lintro.ai.review.models.run_questions import RunQuestions
from lintro.ai.review.session import is_cost_cap_stop

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

__all__ = ["run_with_one_retry"]


async def run_with_one_retry(
    *,
    generate: Callable[[CallShape], Awaitable[RunQuestions]],
    shape: CallShape,
    record: Callable[[RunQuestions], None] | None = None,
) -> RunQuestions:
    """Run the question pass, retrying once for the retryable kinds.

    Args:
        generate: Makes one attempt with the given call shape.
        shape: The first attempt's call shape.
        record: Receives the failed first attempt, marked ``retried``, before
            the retry starts, so a cost-cap stop or SIGTERM during the retry
            still leaves its billed usage with the run (#2826) and the record
            says a retry was made. The returned result supersedes it when the
            retry completes.

    Returns:
        The usable questions (from either attempt), or a failed result that
        carries the first attempt's kind, ``retried`` when a retry was made,
        and the usage of every attempt.
    """
    first = await _attempt(generate=generate, shape=shape)
    if not first.failed or first.failure_kind not in RETRIED_KINDS:
        if first.failed:
            _log_failure(result=first, retrying=False)
        return first
    _log_failure(result=first, retrying=True)
    if record is not None:
        # Marked retried: if the retry is stopped, the run records
        # "<kind>; retried once", not a pass that was never retried (#2803).
        record(replace(first, retried=True))
    retry_shape = (
        CallShape(use_one_shot=True, no_tools=True)
        if first.failure_kind is QuestionFailureKind.TURN_LIMIT
        else shape
    )
    second = await _attempt(generate=generate, shape=retry_shape)
    usage = _sum_usage(first.usage, second.usage)
    if not second.failed:
        logger.info(
            "Per-PR question pass succeeded on retry after {}",
            first.failure_kind,
        )
        return replace(second, usage=usage, retried=True)
    logger.warning(
        "Per-PR question pass failed again on retry ({}): answer starts {}; "
        "reviewing with the rubric alone",
        second.failure_kind,
        second.capture or "<no answer>",
    )
    return replace(first, usage=usage, retried=True)


async def _attempt(
    *,
    generate: Callable[[CallShape], Awaitable[RunQuestions]],
    shape: CallShape,
) -> RunQuestions:
    """Make one attempt, turning a provider error into a classified failure.

    Args:
        generate: Makes one attempt with the given call shape.
        shape: The attempt's call shape.

    Returns:
        The attempt's result; a provider error becomes a failed result of
        kind ``turn_limit`` (keeping the billed usage) or ``call_failed``.

    Raises:
        Exception: A cost-cap stop or the SIGTERM timeout, untouched.
    """
    try:
        return await generate(shape)
    except Exception as exc:
        if is_cost_cap_stop(exc=exc) or SIGTERM_TIMEOUT_MESSAGE in str(exc):
            raise
        if isinstance(exc, AITurnLimitError):
            # A turn-limited CLI call was billed and already charged to the
            # budget; the failed attempt keeps that usage so totals agree.
            return RunQuestions(
                failed=True,
                failure_kind=QuestionFailureKind.TURN_LIMIT,
                usage=ChunkReviewPartial(
                    findings=(),
                    input_tokens=exc.input_tokens,
                    output_tokens=exc.output_tokens,
                    cost_estimate=exc.cost_estimate,
                ),
                # JSON-encoded like every other capture (one line, inert).
                capture=(
                    json.dumps(f"<turn limit: {exc.turns} turns>")
                    if exc.turns is not None
                    else ""
                ),
            )
        logger.warning("Per-PR question call failed ({})", json.dumps(str(exc)))
        return RunQuestions(failed=True, failure_kind=QuestionFailureKind.CALL_FAILED)


def _log_failure(*, result: RunQuestions, retrying: bool) -> None:
    """Log a failed attempt with its kind and the redacted answer start.

    Args:
        result: The failed attempt.
        retrying: Whether one retry follows.
    """
    logger.warning(
        "Per-PR question pass failed ({}): answer starts {}; {}",
        result.failure_kind,
        result.capture or "<no answer>",
        "retrying once" if retrying else "reviewing with the rubric alone",
    )


def _sum_usage(
    first: ChunkReviewPartial,
    second: ChunkReviewPartial,
) -> ChunkReviewPartial:
    """Return the usage of both attempts together.

    Args:
        first: The first attempt's usage.
        second: The retry's usage.

    Returns:
        The summed usage.
    """
    return ChunkReviewPartial(
        findings=(),
        input_tokens=first.input_tokens + second.input_tokens,
        output_tokens=first.output_tokens + second.output_tokens,
        cost_estimate=first.cost_estimate + second.cost_estimate,
    )
