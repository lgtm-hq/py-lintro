"""Degrade to the main pass when an optional deeper pass fails (issue #2395).

Depth 2 and depth 3 add provider calls *around* the main chunk review: the
generated-questions pass runs before it and the adversarial sweep after it. By
the time either can fail, the main pass has either not run yet or has already
been paid for, so an :class:`~lintro.ai.exceptions.AIError` from one of them is
a loss of depth, not a loss of the chunk. Letting it propagate aborted the
chunk and discarded findings the run had already bought.

:func:`run_degradable_depth_pass` runs one such call and, on ``AIError``,
returns no value plus the
:class:`~lintro.ai.review.models.coverage_degradation.CoverageDegradation` the
chunk records. That is the same #2003 channel the findings cap and the #2269
synthesis failure use, so the degraded pass reaches the terminal warning, both
GitHub surfaces and the JSON/MCP payloads without a parallel mechanism.

:class:`~lintro.ai.exceptions.AICostBudgetExceededError` is deliberately not
caught: the cost cap is a graceful *stop* the run finalizes a partial review
on, so swallowing it here would let the run keep spending past the ceiling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar

from loguru import logger

from lintro.ai.exceptions import AICostBudgetExceededError, AIError
from lintro.ai.review.models.coverage_degradation import CoverageDegradation

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from lintro.ai.review.enums.coverage_degradation_reason import (
        CoverageDegradationReason,
    )

__all__ = ["DEPTH_PASS_NO_CAP", "run_degradable_depth_pass"]

#: ``findings_cap`` stamped on a failed depth pass. The extra passes carry no
#: per-call findings ceiling, so this is a placeholder rather than a real cap;
#: :data:`~lintro.ai.review.models.review_metadata._CAP_REASONS` excludes these
#: reasons so it can never win the ``findings_cap_applied`` minimum.
DEPTH_PASS_NO_CAP: int = 0

_PassResult = TypeVar("_PassResult")


async def run_degradable_depth_pass(
    *,
    call: Coroutine[Any, Any, _PassResult],
    reason: CoverageDegradationReason,
    chunk_index: int,
    label: str,
) -> tuple[_PassResult | None, tuple[CoverageDegradation, ...]]:
    """Await one optional depth >= 2 pass, degrading instead of failing.

    Args:
        call: The coroutine that runs the pass.
        reason: Degradation reason recorded when the pass fails.
        chunk_index: Zero-based index of the chunk the pass belongs to.
        label: Human-readable name of the pass, used in the log line.

    Returns:
        The pass's result and no degradations, or ``None`` and the single
        degradation the chunk must report.

    Raises:
        AICostBudgetExceededError: When the session cost ceiling is hit. The
            cap is a graceful stop for the whole run, not a degraded pass.
    """
    try:
        return await call, ()
    except AICostBudgetExceededError:
        raise
    except AIError as exc:
        logger.warning(
            f"The {label} failed for chunk {chunk_index} ({exc}); keeping "
            "the main pass's result and recording degraded coverage.",
        )
        return None, (
            CoverageDegradation(
                reason=reason,
                chunk_index=chunk_index,
                findings_cap=DEPTH_PASS_NO_CAP,
            ),
        )
