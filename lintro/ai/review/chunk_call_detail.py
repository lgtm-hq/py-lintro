"""Per-chunk call detail lifted off a finished chunk task (lintro-ops #37).

The fan-out in :mod:`lintro.ai.review.chunk_runner` records each chunk's
queued/in-flight split; these helpers add the main provider call's own wall
time and transport-reported turn count, which travel on the chunk partial.
Kept out of the runner so it stays under the #2301 module-size ratchet.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lintro.ai.review.merge import ChunkReviewPartial

__all__ = ["_call_detail", "_finished_partial"]


def _finished_partial(
    *,
    task: asyncio.Future[ChunkReviewPartial],
) -> ChunkReviewPartial | None:
    """Return a task's partial when it finished cleanly, else ``None``.

    Args:
        task: The chunk review task.

    Returns:
        The completed partial, or ``None`` when the task is unfinished,
        cancelled, or failed.
    """
    if not task.done() or task.cancelled() or task.exception() is not None:
        return None
    return task.result()


def _call_detail(
    *,
    partial: ChunkReviewPartial | None,
) -> tuple[float, int | None]:
    """Return the main call's wall time and turn count for the timings.

    Args:
        partial: The completed chunk partial, or ``None`` when the chunk did
            not complete a main call.

    Returns:
        ``(provider_seconds, turns)``; ``(0.0, None)`` for an incomplete chunk.
    """
    if partial is None:
        return 0.0, None
    return partial.provider_seconds, partial.turns
