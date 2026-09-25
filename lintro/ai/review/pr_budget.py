"""Per-PR review budget: total spend across every round of one PR (#2796).

``ai.max_cost_usd`` bounds one round. ``ai.review_pr_budget_usd`` bounds the
PR's cumulative review spend (``ReviewState.pr_spend_usd``: every round of the
pull request, push, delta, full and targeted on-request alike) plus the running
round, so a PR that keeps being pushed cannot keep spending without limit. The
total is written at every mid-run checkpoint and at the final write and never
decreases, so neither an interrupted round nor run-history pruning loses spend.

Enforcement mirrors ``max_cost_usd`` exactly (:func:`cap_is_enforced`): the
``LINTRO_AI_REVIEW_PR_BUDGET_USD`` overlay always enforces, a YAML budget only
when spend is billed or estimated, so under a subscription CLI transport a
YAML-only budget is display-only. Unset means no check and no sticky line.

The budget rides on the round's :class:`~lintro.ai.budget.CostBudget`: its
ceiling becomes the tighter of the round cap and what is left of the PR
budget, so the existing checks (before each chunk and each later pass) stop
the round, and a budget already spent stops it before the first provider
call. The stop is graceful: findings so far are posted and coverage is
checkpointed like any cost-cap stop, only the reason differs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from lintro.ai.enums.cost_basis import CostBasis
from lintro.ai.review.cost_cap import cap_is_enforced, cost_cap_reason

if TYPE_CHECKING:
    from lintro.ai.enums.config_source import ConfigSource
    from lintro.ai.review.models.review_state import ReviewState
    from lintro.ai.review.session import ReviewSessionOptions

__all__ = [
    "PR_BUDGET_REASON_PREFIX",
    "RUNTIME_BOUND_NOTE",
    "PrBudget",
    "cost_stop_reason",
    "resolve_pr_budget",
    "round_cap",
    "round_ceiling",
    "run_cost_ceiling",
]

#: Every PR-budget ``stopped_reason`` starts with this; the CI outcome
#: classifier keys its ``pr_budget`` outcome on it (a test pins the two).
PR_BUDGET_REASON_PREFIX = "PR budget"

#: Appended where a PR-budget figure is shown on an unpriceable basis: on the
#: subscription CLI transport the dollars are a runtime bound, not a bill.
RUNTIME_BOUND_NOTE = "runtime bound on the cli transport"


@dataclass(frozen=True, slots=True)
class PrBudget:
    """The PR's review budget as resolved for one round.

    Attributes:
        budget_usd: The configured ``ai.review_pr_budget_usd``.
        prior_spend_usd: The PR's cumulative review spend before this round
            (:attr:`ReviewState.review_spend_usd`).
        enforced: Whether reaching the budget stops the round.
        unpriceable: Whether spend is a runtime bound rather than dollars
            (the subscription CLI transport).
    """

    budget_usd: float
    prior_spend_usd: float
    enforced: bool
    unpriceable: bool = False

    @property
    def remaining_usd(self) -> float:
        """Return what this round may still spend, never below zero."""
        return max(0.0, self.budget_usd - self.prior_spend_usd)


def resolve_pr_budget(
    *,
    budget_usd: float | None,
    source: ConfigSource,
    basis: CostBasis,
    prior_state: ReviewState | None,
) -> PrBudget | None:
    """Resolve the PR budget for this round, or None when it is unset.

    Args:
        budget_usd: The effective ``ai.review_pr_budget_usd``.
        source: Where it was set (flag, env, config or default).
        basis: How this round's spend is measured.
        prior_state: The PR's persisted review state, if any.

    Returns:
        The resolved budget, or None when no budget is configured.
    """
    if budget_usd is None:
        return None
    return PrBudget(
        budget_usd=budget_usd,
        prior_spend_usd=(
            prior_state.review_spend_usd if prior_state is not None else 0.0
        ),
        enforced=cap_is_enforced(source=source, basis=basis),
        unpriceable=basis is CostBasis.UNPRICEABLE,
    )


def round_cap(*, options: ReviewSessionOptions) -> float | None:
    """Return the enforced ``ai.max_cost_usd`` for the round, or None.

    Args:
        options: The run's session options.

    Returns:
        The round cap when it is enforced, otherwise None.
    """
    return options.ai_config.max_cost_usd if options.enforce_cost_cap else None


def _binding(*, round_cap: float | None, pr_budget: PrBudget | None) -> PrBudget | None:
    """Return the PR budget when it, not the round cap, sets the ceiling.

    Args:
        round_cap: The enforced round cap, or None.
        pr_budget: The resolved PR budget, or None.

    Returns:
        The PR budget when it is enforced and at or under the round cap,
        otherwise None.
    """
    if pr_budget is None or not pr_budget.enforced:
        return None
    if round_cap is None or pr_budget.remaining_usd <= round_cap:
        return pr_budget
    return None


def round_ceiling(
    *,
    round_cap: float | None,
    pr_budget: PrBudget | None,
) -> float | None:
    """Return the round's spend ceiling: the tighter of the two limits.

    Args:
        round_cap: The enforced ``ai.max_cost_usd``, or None.
        pr_budget: The resolved PR budget, or None.

    Returns:
        The ceiling for this round's ``CostBudget``, or None when uncapped.
        Zero when the PR budget is already spent, which stops the round
        before its first provider call.
    """
    binding = _binding(round_cap=round_cap, pr_budget=pr_budget)
    return binding.remaining_usd if binding is not None else round_cap


def run_cost_ceiling(*, options: ReviewSessionOptions) -> float | None:
    """Return the ceiling for the run's ``CostBudget``.

    Args:
        options: The run's session options.

    Returns:
        :func:`round_ceiling` of the enforced round cap and the PR budget.
    """
    return round_ceiling(
        round_cap=round_cap(options=options),
        pr_budget=options.pr_budget,
    )


def cost_stop_reason(*, round_cap: float | None, pr_budget: PrBudget | None) -> str:
    """Build the ``stopped_reason`` for a round a spend ceiling stopped.

    Args:
        round_cap: The enforced ``ai.max_cost_usd``, or None.
        pr_budget: The resolved PR budget, or None.

    Returns:
        ``"PR budget ($40.00) reached"`` when the PR budget set the ceiling,
        otherwise the round cap's reason.
    """
    binding = _binding(round_cap=round_cap, pr_budget=pr_budget)
    if binding is not None:
        reason = f"{PR_BUDGET_REASON_PREFIX} (${binding.budget_usd:.2f}) reached"
        if binding.unpriceable:
            reason += f" ({RUNTIME_BOUND_NOTE})"
        return reason
    return cost_cap_reason(cap=round_cap)
