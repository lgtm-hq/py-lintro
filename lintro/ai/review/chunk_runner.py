"""Chunk fan-out for the built-in review (issue #2301).

The orchestrator decides *what* to review; this module runs it:

* :func:`review_all_chunks` — bounded-concurrency fan-out over the planned
  chunks, with graceful cost-cap / timeout / SIGTERM stops and an incremental
  sink so completed work survives an aborted run.
* :func:`chunk_pass.review_chunk` — the depth-controlled pass over one chunk,
  invoked once per chunk from here.

The run-scope inputs travel as one frozen
:class:`~lintro.ai.review.session.ChunkRunPlan` rather than the ~18 keywords
each layer used to forward by hand; the per-chunk differences (progress
tracker, generated-checklist id) are applied with :func:`dataclasses.replace`.

Every provider call below goes through
:mod:`lintro.ai.review.provider_call`, the single seam tests replace.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from loguru import logger

from lintro.ai.exceptions import (
    AICostBudgetExceededError,
    AIError,
    AIProviderError,
)
from lintro.ai.review.checklist_pass import GENERATED_CHECKLIST_ID_STRIDE
from lintro.ai.review.chunk_pass import review_chunk_with_progress
from lintro.ai.review.exceptions import ReviewExecutionError
from lintro.ai.review.interrupt import (
    SIGTERM_TIMEOUT_MESSAGE,
    sigterm_timeout_error,
)
from lintro.ai.review.merge import ChunkReviewPartial
from lintro.ai.review.progress import StepTrackingProgress
from lintro.ai.review.session import (
    aborted_before_completion,
    is_cost_cap_stop,
    is_timeout_stop,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from lintro.ai.review.models.review_chunk import ReviewChunk
    from lintro.ai.review.session import ChunkRunPlan

__all__ = ["review_all_chunks"]


async def _review_one_chunk_until_stop(
    *,
    chunk: ReviewChunk,
    plan: ChunkRunPlan,
) -> ChunkReviewPartial:
    """Review a single chunk, aborting persistably when the stop event is set.

    Args:
        chunk: The only remaining chunk.
        plan: Run-scope inputs for the review.

    Returns:
        The completed chunk partial.

    Raises:
        AIProviderError: When SIGTERM arrives before the chunk finishes.
    """
    # A lone chunk never waits on the concurrency semaphore, so its queued
    # time is zero by construction; only the in-flight span is measured.
    started = time.monotonic()
    stop = plan.stop
    timings = plan.timings
    review_task = asyncio.ensure_future(
        review_chunk_with_progress(
            chunk_index=0,
            chunk=chunk,
            total_chunks=1,
            plan=plan,
        ),
    )

    def _record(*, failed: bool) -> None:
        """Record the lone chunk's in-flight span.

        Args:
            failed: True when the chunk ended in an error or a stop.
        """
        if timings is None:
            return
        timings.add_chunk(
            chunk_index=0,
            files=len(chunk.files),
            queued_seconds=0.0,
            in_flight_seconds=time.monotonic() - started,
            failed=failed,
        )

    async def _await_recorded() -> ChunkReviewPartial:
        """Await the chunk review, recording its span either way.

        Returns:
            The completed chunk partial.
        """
        unfinished = True
        try:
            single = await review_task
            unfinished = False
        finally:
            _record(failed=unfinished)
        return single

    if stop is None:
        return await _await_recorded()
    stop_task = asyncio.ensure_future(stop.wait())
    try:
        done, _pending = await asyncio.wait(
            {review_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_task in done and stop.is_set():
            if not review_task.done():
                review_task.cancel()
                with suppress(asyncio.CancelledError):
                    await review_task
                _record(failed=True)
                raise AIProviderError(SIGTERM_TIMEOUT_MESSAGE) from TimeoutError(
                    "SIGTERM",
                )
            # The agent may have died from a forwarded TERM before the
            # stop task won. Treat that failure as the persistable
            # SIGTERM timeout instead of aborting without coverage.
            if review_task.cancelled() or review_task.exception() is not None:
                _record(failed=True)
                raise AIProviderError(SIGTERM_TIMEOUT_MESSAGE) from TimeoutError(
                    "SIGTERM",
                )
            _record(failed=False)
            return review_task.result()
        return await _await_recorded()
    finally:
        if not stop_task.done():
            stop_task.cancel()
            with suppress(asyncio.CancelledError):
                await stop_task


#: Outcome of one chunk task: its index paired with a partial or the failure.
_ChunkOutcome = tuple[int, "ChunkReviewPartial | Exception"]


@dataclass(frozen=True, slots=True, kw_only=True)
class _CompletionSink:
    """Where a finished chunk partial is published as soon as it lands.

    Attributes:
        completed: Optional list each completed partial is appended to, so a
            caller can recover the work done before an aborted run.
        on_complete: Optional callback invoked with that list after each
            append, so CI can write an incremental coverage part.
    """

    completed: list[ChunkReviewPartial] | None = None
    on_complete: Callable[[list[ChunkReviewPartial]], None] | None = None

    def record(self, *, partial: ChunkReviewPartial) -> None:
        """Publish one completed chunk partial.

        Args:
            partial: The partial that just completed.
        """
        if self.completed is None:
            return
        self.completed.append(partial)
        if self.on_complete is not None:
            self.on_complete(self.completed)


def _harvest_completed(
    *,
    tasks: list[asyncio.Future[_ChunkOutcome]],
    partials: list[ChunkReviewPartial | None],
    sink: _CompletionSink,
) -> None:
    """Record every sibling that finished before a graceful stop landed.

    A cost-cap / timeout / SIGTERM stop is an expected halt, so the chunks a
    parallel worker already finished must not be discarded with the run.

    Args:
        tasks: Every task of the fan-out, finished or not.
        partials: Index-keyed slots the harvested partials are written into.
        sink: Completion sink notified for each newly recorded partial.
    """
    for task in tasks:
        if not task.done() or task.cancelled():
            continue
        try:
            other_index, other = task.result()
        except Exception:
            logger.opt(exception=True).debug(
                "Skipping a failed sibling while harvesting completed chunks",
            )
            continue  # nosec B112 - harvest only finished siblings; a failed result() is not this stop's outcome
        if isinstance(other, Exception) or partials[other_index] is not None:
            continue
        partials[other_index] = other
        sink.record(partial=other)


async def _run_chunk(
    *,
    chunk_index: int,
    chunk: ReviewChunk,
    total_chunks: int,
    plan: ChunkRunPlan,
    semaphore: asyncio.Semaphore,
) -> _ChunkOutcome:
    """Review one chunk under the concurrency semaphore.

    Failures are returned rather than raised so the caller keeps the
    chunk-to-outcome mapping that ``as_completed`` would otherwise lose.

    Args:
        chunk_index: Position of the chunk in the run.
        chunk: The chunk to review.
        total_chunks: Number of chunks in the run.
        plan: Run-scope inputs shared by every chunk.
        semaphore: Gate enforcing ``plan.max_parallel_calls``.

    Returns:
        The chunk index paired with its partial or the exception raised.
    """
    chunk_plan = replace(
        plan,
        progress=StepTrackingProgress(plan.progress),
        next_generated_checklist_id=(
            plan.next_generated_checklist_id
            + chunk_index * GENERATED_CHECKLIST_ID_STRIDE
        ),
    )
    # Queued time is measured from task creation to semaphore admission, so a
    # run bottlenecked by ``max_parallel_calls`` is distinguishable from one
    # bottlenecked by provider latency (#2148).
    queued_at = time.monotonic()
    admitted_at: float | None = None
    failed = True
    try:
        async with semaphore:
            admitted_at = time.monotonic()
            outcome = (
                chunk_index,
                await review_chunk_with_progress(
                    chunk_index=chunk_index,
                    chunk=chunk,
                    total_chunks=total_chunks,
                    plan=chunk_plan,
                ),
            )
    except Exception as exc:
        return chunk_index, exc
    else:
        failed = False
        return outcome
    finally:
        # Recorded outside the semaphore so a chunk cancelled while still
        # queued (cost-cap stop, SIGTERM) reports its wait with no in-flight
        # time rather than vanishing from the breakdown.
        if plan.timings is not None:
            now = time.monotonic()
            plan.timings.add_chunk(
                chunk_index=chunk_index,
                files=len(chunk.files),
                queued_seconds=(
                    (admitted_at if admitted_at is not None else now) - queued_at
                ),
                in_flight_seconds=(
                    now - admitted_at if admitted_at is not None else 0.0
                ),
                failed=failed,
            )


async def _stop_as_timeout(*, stop: asyncio.Event) -> _ChunkOutcome:
    """Surface SIGTERM as a persistable timeout outcome.

    Args:
        stop: Event a SIGTERM/SIGINT handler sets.

    Returns:
        A sentinel index paired with the persistable timeout error.
    """
    await stop.wait()
    return -1, sigterm_timeout_error()


def _as_stop_outcome(
    *,
    outcome: ChunkReviewPartial | Exception,
    stop: asyncio.Event | None,
) -> ChunkReviewPartial | Exception:
    """Rewrite a failure that a set stop event explains as a SIGTERM timeout.

    A forwarded TERM can kill the isolated agent and surface a non-timeout
    provider error; persisting it as SIGTERM keeps the coverage.

    Args:
        outcome: The chunk's outcome.
        stop: Event a SIGTERM/SIGINT handler sets, when one is installed.

    Returns:
        The original outcome, or the persistable SIGTERM timeout error.
    """
    if (
        isinstance(outcome, Exception)
        and stop is not None
        and stop.is_set()
        and not is_cost_cap_stop(exc=outcome)
    ):
        return sigterm_timeout_error()
    return outcome


def _is_graceful_stop(*, outcome: ChunkReviewPartial | Exception) -> bool:
    """Report whether an outcome is an expected halt rather than a failure.

    Args:
        outcome: The chunk's outcome.

    Returns:
        True for a cost-cap, timeout or SIGTERM stop.
    """
    return isinstance(
        outcome,
        (ReviewExecutionError, AICostBudgetExceededError),
    ) or (isinstance(outcome, Exception) and is_timeout_stop(exc=outcome))


async def _collect_outcomes(
    *,
    tasks: list[asyncio.Future[_ChunkOutcome]],
    review_count: int,
    plan: ChunkRunPlan,
    sink: _CompletionSink,
) -> list[ChunkReviewPartial]:
    """Drain the fan-out, publishing partials and stopping on the first halt.

    ``tasks`` is every chunk review plus the optional stop task, of which the
    first ``review_count`` are chunk reviews; ``plan`` carries the run-scope
    inputs, and ``sink`` is notified for each partial recorded. Returns the
    completed partials in chunk order.

    A graceful stop (``AIError`` / ``ReviewExecutionError``) is re-raised as it
    arrived, after the sibling harvest; any other chunk failure is re-raised
    wrapped by :func:`aborted_before_completion` once the loop has ended. The
    exception objects are built elsewhere, so the raises here name locals
    rather than classes and the sections above stay prose.
    """
    partials: list[ChunkReviewPartial | None] = [None] * review_count
    first_error: ReviewExecutionError | None = None
    # The chunks recorded by this loop, as opposed to the ones a graceful-stop
    # harvest wrote into ``partials``: the abort message reports what the run
    # itself finished.
    recorded: list[int] = []
    remaining_reviews = review_count
    for finished in asyncio.as_completed(tasks):
        chunk_index, outcome = await finished
        if chunk_index >= 0:
            remaining_reviews -= 1
        outcome = _as_stop_outcome(outcome=outcome, stop=plan.stop)
        if _is_graceful_stop(outcome=outcome):
            # A cost-cap / timeout / SIGTERM stop is an expected halt. Harvest
            # siblings that already finished so a timeout on one worker cannot
            # drop coverage the other worker wrote.
            _harvest_completed(tasks=tasks, partials=partials, sink=sink)
            if isinstance(outcome, (AIError, ReviewExecutionError)):
                raise outcome
        if isinstance(outcome, Exception):
            if first_error is None:
                first_error = aborted_before_completion(
                    cause=outcome,
                    provider=plan.provider,
                    chunk_index=chunk_index,
                    total_chunks=review_count,
                    step="reviewing",
                    completed_chunks=len(recorded),
                )
            break
        partials[chunk_index] = outcome
        sink.record(partial=outcome)
        recorded.append(chunk_index)
        if remaining_reviews == 0:
            break

    if first_error is not None:
        raise first_error

    # Index-keyed merge keeps completion order from scrambling chunk order.
    return [partial for partial in partials if partial is not None]


async def review_all_chunks(
    *,
    chunks: list[ReviewChunk],
    plan: ChunkRunPlan,
    completed_sink: list[ChunkReviewPartial] | None = None,
    on_chunk_complete: Callable[[list[ChunkReviewPartial]], None] | None = None,
) -> list[ChunkReviewPartial]:
    """Review all chunks with bounded concurrency.

    When ``completed_sink`` is provided, each successfully reviewed chunk's
    partial is appended to it as soon as it completes. This lets the caller
    recover the chunks reviewed so far if the run aborts mid-way (e.g. the cost
    cap is reached), enabling a graceful partial review instead of discarding
    all completed work. ``on_chunk_complete`` is invoked with the sink after
    each append so CI can write an incremental coverage part.

    Chunks are reviewed concurrently under a semaphore capped by
    ``plan.max_parallel_calls``. Callers that enforce a cost cap pass ``1`` so
    the resume queue cannot invert (issue #2154). A ``ReviewExecutionError`` or
    a cost-cap stop cancels the remaining work and propagates to
    ``run_review_async``. Depth >= 2 assigns each chunk a disjoint
    generated-checklist id range so merge stays deterministic under fan-out.
    ``plan.stop`` is set by a SIGTERM/SIGINT handler so an in-flight chunk can
    be cancelled and completed siblings persisted (#2156). ``plan.timings``
    records each chunk's semaphore-queued and in-flight split (#2148).

    Args:
        chunks: The chunks to review, in plan order.
        plan: Run-scope inputs shared by every chunk.
        completed_sink: Optional list completed partials are appended to.
        on_chunk_complete: Optional callback invoked with the sink after each
            append.

    Returns:
        The completed partials, in chunk order.
    """
    sink = _CompletionSink(completed=completed_sink, on_complete=on_chunk_complete)
    if not chunks:
        # A resumed round can filter every chunk away. The caller skips this
        # function then, but the fast path below indexes chunks[0], so the
        # empty case is answered here rather than left as a footgun.
        return []
    if len(chunks) == 1:
        single = await _review_one_chunk_until_stop(chunk=chunks[0], plan=plan)
        sink.record(partial=single)
        return [single]

    # Bounded concurrency on the caller's event loop: chunk reviews are
    # provider I/O, so tasks under a semaphore keep the ``max_parallel_calls``
    # ceiling without threads. The ceiling arrives already resolved --
    # ``plan_run`` sets it to 1 for a cost-capped run so the resume queue
    # cannot invert (#2154) -- and above 1 the accepted overshoot bound is the
    # n-1 calls documented on ``CostBudget.execute``.
    semaphore = asyncio.Semaphore(min(len(chunks), plan.max_parallel_calls))
    tasks: list[asyncio.Future[_ChunkOutcome]] = [
        asyncio.ensure_future(
            _run_chunk(
                chunk_index=chunk_index,
                chunk=chunk,
                total_chunks=len(chunks),
                plan=plan,
                semaphore=semaphore,
            ),
        )
        for chunk_index, chunk in enumerate(chunks)
    ]
    review_count = len(tasks)
    if plan.stop is not None:
        tasks.append(asyncio.ensure_future(_stop_as_timeout(stop=plan.stop)))
    try:
        return await _collect_outcomes(
            tasks=tasks,
            review_count=review_count,
            plan=plan,
            sink=sink,
        )
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        # Drain the cancellations so no chunk review (and no CLI child
        # process) outlives this function.
        await asyncio.gather(*tasks, return_exceptions=True)
