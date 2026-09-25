"""ConfigSource × cost_basis enforcement for ``max_cost_usd`` (#2154).

Flag and env overlays are operator intent for this run and enforce on
every cost basis, including subscription CLI. Committed YAML is repo
policy and is transport-unaware: it enforces when real money is at
stake (``billed`` or ``estimated``) and is display-only on
``unpriceable``. Unset stays uncapped. Shadow pricing (``~$``) is
unchanged — measurement is not enforcement.
"""

from __future__ import annotations

from lintro.ai.enums.config_source import ConfigSource
from lintro.ai.enums.cost_basis import CostBasis
from lintro.ai.exceptions import AICostBudgetExceededError

__all__ = ["cap_is_enforced", "cost_cap_reason", "is_cost_cap_stop"]


def cap_is_enforced(*, source: ConfigSource, basis: CostBasis) -> bool:
    """Return whether a resolved dollar cap must stop the run.

    Args:
        source: Where ``max_cost_usd`` was set.
        basis: How the run's spend is measured.

    Returns:
        True when the orchestrator must treat the cap as a hard stop.
    """
    if source in (ConfigSource.FLAG, ConfigSource.ENV):
        return True
    if source is ConfigSource.CONFIG:
        return basis in (CostBasis.BILLED, CostBasis.ESTIMATED)
    return False


def is_cost_cap_stop(*, exc: BaseException) -> bool:
    """Return whether an exception represents a graceful cost-cap stop.

    The cost cap can surface either as a raw
    :class:`~lintro.ai.exceptions.AICostBudgetExceededError` (when the
    top-of-loop ``budget.check()`` raises) or wrapped inside a
    :class:`~lintro.ai.review.exceptions.ReviewExecutionError` (when an
    intra-chunk check raises and the chunk failure is wrapped). Both cases are
    detected by walking the ``__cause__`` chain so a cost-cap stop is never
    misclassified as a genuine provider error, and vice versa. A PR-budget
    stop (#2796) is the same exception with a different ceiling.

    Args:
        exc: The exception raised while reviewing chunks.

    Returns:
        True when the underlying cause is a cost-cap exhaustion.
    """
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, AICostBudgetExceededError):
            return True
        current = current.__cause__
    return False


def cost_cap_reason(*, cap: float | None) -> str:
    """Build the human-readable ``stopped_reason`` for a cost-cap stop.

    Args:
        cap: The configured ``ai.max_cost_usd`` ceiling, if any.

    Returns:
        A message such as ``"cost cap ($0.50) reached"``.
    """
    if cap is None:
        return "cost cap reached"
    return f"cost cap (${cap:.2f}) reached"
