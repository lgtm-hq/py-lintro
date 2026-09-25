"""Per-PR review budget, ``ai.review_pr_budget_usd`` (#2796, #2627 PR 3).

Spend is the PR's cumulative review spend (``ReviewState.pr_spend_usd``,
written at every checkpoint and the final write, never decreasing) plus the
running round; ``test_pr_spend_2796.py`` covers how that total is kept.
Enforcement mirrors ``ai.max_cost_usd``: the env overlay always enforces, a
YAML budget only on a billed or estimated basis. An enforced budget becomes
the round's ``CostBudget`` ceiling (the tighter of the two), so a spent budget
stops the round before its first provider call and a crossed one stops it at
the next check, with a ``PR budget`` stop reason. Parallelism is unchanged
(ruling 16).
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.config_overrides import ENV_REVIEW_PR_BUDGET_USD
from lintro.ai.enums import AITransport
from lintro.ai.enums.config_source import ConfigSource
from lintro.ai.enums.cost_basis import CostBasis
from lintro.ai.exceptions import AIConfigOverrideError
from lintro.ai.providers.capabilities import ProviderCapabilities
from lintro.ai.providers.response import AIResponse
from lintro.ai.review.group_labels import REL_SINGLE_FILE
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.review_chunk import ReviewChunk
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.models.run_usage import RunUsage
from lintro.ai.review.orchestrator import run_review
from lintro.ai.review.pr_budget import (
    PR_BUDGET_REASON_PREFIX,
    PrBudget,
    cost_stop_reason,
    resolve_pr_budget,
    round_ceiling,
)
from lintro.ai.review.run_planning import plan_run
from lintro.ai.review.session import ReviewSessionOptions, stop_hint
from lintro.ai.review.state_store import load_ci_state
from lintro.ai.review.sticky.history import _this_run_section
from lintro.ai.review.timings import ReviewTimingRecorder

_ENFORCED = PrBudget(budget_usd=40.0, prior_spend_usd=0.0, enforced=True)


def _state(*costs: float) -> ReviewState:
    """Return a review state whose rounds cost ``costs``.

    Args:
        *costs: One ``usage.cost`` per recorded round.

    Returns:
        The state.
    """
    runs = tuple(RunRecord(usage=RunUsage(cost=cost)) for cost in costs)
    return ReviewState(runs=runs)


@pytest.mark.parametrize(
    ("source", "basis", "enforced"),
    [
        pytest.param(ConfigSource.ENV, CostBasis.UNPRICEABLE, True, id="env-cli"),
        pytest.param(ConfigSource.FLAG, CostBasis.UNPRICEABLE, True, id="flag-cli"),
        pytest.param(
            ConfigSource.CONFIG,
            CostBasis.UNPRICEABLE,
            False,
            id="yaml-cli-display-only",
        ),
        pytest.param(ConfigSource.CONFIG, CostBasis.BILLED, True, id="yaml-api"),
        pytest.param(ConfigSource.CONFIG, CostBasis.ESTIMATED, True, id="yaml-est"),
    ],
)
def test_enforcement_mirrors_the_round_cap(
    source: ConfigSource,
    basis: CostBasis,
    enforced: bool,
) -> None:
    """The budget is a hard stop exactly where ``max_cost_usd`` would be.

    Args:
        source: Where the budget was set.
        basis: How the round's spend is measured.
        enforced: Whether the budget must stop the round.
    """
    budget = resolve_pr_budget(
        budget_usd=40.0,
        source=source,
        basis=basis,
        prior_state=None,
    )

    assert_that(budget).is_not_none()
    assert budget is not None
    assert_that(budget.enforced).is_equal_to(enforced)


def test_spend_is_the_sum_over_every_recorded_round() -> None:
    """Prior spend sums ``usage.cost`` across rounds of any kind."""
    budget = resolve_pr_budget(
        budget_usd=40.0,
        source=ConfigSource.ENV,
        basis=CostBasis.UNPRICEABLE,
        prior_state=_state(10.03, 7.63, 0.5),
    )

    assert budget is not None
    assert_that(budget.prior_spend_usd).is_close_to(18.16, 1e-9)
    assert_that(budget.remaining_usd).is_close_to(21.84, 1e-9)


def test_an_unset_budget_resolves_to_none() -> None:
    """No budget, no check: the default keeps today's behaviour."""
    budget = resolve_pr_budget(
        budget_usd=None,
        source=ConfigSource.DEFAULT,
        basis=CostBasis.BILLED,
        prior_state=_state(100.0),
    )

    assert_that(budget).is_none()


def test_a_first_round_has_no_prior_spend() -> None:
    """A PR with no state yet has spent nothing."""
    budget = resolve_pr_budget(
        budget_usd=40.0,
        source=ConfigSource.ENV,
        basis=CostBasis.BILLED,
        prior_state=None,
    )

    assert budget is not None
    assert_that(budget.prior_spend_usd).is_equal_to(0.0)


@pytest.mark.parametrize(
    ("round_cap", "pr_budget", "ceiling"),
    [
        pytest.param(None, None, None, id="both-unset"),
        pytest.param(5.0, None, 5.0, id="round-cap-only"),
        pytest.param(None, _ENFORCED, 40.0, id="pr-budget-only"),
        pytest.param(
            5.0,
            PrBudget(budget_usd=40.0, prior_spend_usd=38.0, enforced=True),
            2.0,
            id="pr-budget-tighter",
        ),
        pytest.param(
            5.0,
            PrBudget(budget_usd=40.0, prior_spend_usd=10.0, enforced=True),
            5.0,
            id="round-cap-tighter",
        ),
        pytest.param(
            None,
            PrBudget(budget_usd=40.0, prior_spend_usd=41.0, enforced=True),
            0.0,
            id="already-spent",
        ),
        pytest.param(
            None,
            PrBudget(budget_usd=40.0, prior_spend_usd=41.0, enforced=False),
            None,
            id="display-only",
        ),
    ],
)
def test_the_round_ceiling_is_the_tighter_limit(
    round_cap: float | None,
    pr_budget: PrBudget | None,
    ceiling: float | None,
) -> None:
    """The round's ``CostBudget`` gets the tighter of the two ceilings.

    Args:
        round_cap: The enforced round cap.
        pr_budget: The resolved PR budget.
        ceiling: The expected ceiling.
    """
    assert_that(round_ceiling(round_cap=round_cap, pr_budget=pr_budget)).is_equal_to(
        ceiling,
    )


def test_the_stop_reason_names_the_limit_that_stopped_the_round() -> None:
    """A PR-budget stop and a round-cap stop read differently."""
    tight = PrBudget(budget_usd=40.0, prior_spend_usd=39.0, enforced=True)

    assert_that(cost_stop_reason(round_cap=5.0, pr_budget=tight)).is_equal_to(
        "PR budget ($40.00) reached",
    )
    assert_that(cost_stop_reason(round_cap=0.5, pr_budget=tight)).is_equal_to(
        "cost cap ($0.50) reached",
    )
    assert_that(cost_stop_reason(round_cap=0.5, pr_budget=None)).is_equal_to(
        "cost cap ($0.50) reached",
    )


def test_an_unpriceable_basis_notes_the_runtime_bound() -> None:
    """On the CLI transport the stop reason says the dollars are a bound."""
    tight = PrBudget(
        budget_usd=40.0,
        prior_spend_usd=39.0,
        enforced=True,
        unpriceable=True,
    )

    assert_that(cost_stop_reason(round_cap=None, pr_budget=tight)).is_equal_to(
        "PR budget ($40.00) reached (runtime bound on the cli transport)",
    )


def test_resolving_on_the_cli_basis_marks_it_unpriceable() -> None:
    """The basis flows into the budget so the reason can carry the note."""
    budget = resolve_pr_budget(
        budget_usd=40.0,
        source=ConfigSource.ENV,
        basis=CostBasis.UNPRICEABLE,
        prior_state=None,
    )

    assert budget is not None
    assert_that(budget.unpriceable).is_true()


def test_the_stop_hint_names_the_budget_and_its_overlay() -> None:
    """The operator hint for a PR-budget stop says what to raise."""
    hint = stop_hint(
        stopped_reason=f"{PR_BUDGET_REASON_PREFIX} ($40.00) reached",
        ai_config=AIConfig(),
    )

    assert_that(hint).contains("ai.review_pr_budget_usd")
    assert_that(hint).contains(ENV_REVIEW_PR_BUDGET_USD)


def test_an_enforced_budget_leaves_parallelism_as_configured() -> None:
    """Ruling 16: the budget does not serialise chunk calls.

    Overshoot is bounded by ``CostBudget`` reservations to the calls already
    in flight, the same trade an unenforced-parallel round cap accepts.
    """
    config = AIConfig(transport=AITransport.API, max_parallel_calls=4)
    options = ReviewSessionOptions(
        provider=_provider(),
        ai_config=config,
        checklist_items=[],
        checklist_text="",
        classifications=[],
        enforce_cost_cap=False,
        pr_budget=PrBudget(budget_usd=40.0, prior_spend_usd=39.0, enforced=True),
    )

    with patch(
        "lintro.ai.review.run_planning.resolve_review_chunks",
        return_value=_chunks(),
    ):
        plan = plan_run(
            context=_context(),
            options=options,
            timings=ReviewTimingRecorder(),
        )

    assert_that(plan.max_parallel_calls).is_equal_to(4)
    # The budget is still the ceiling: parallel, but bounded.
    assert_that(plan.budget.max_cost_usd).is_close_to(1.0, 1e-9)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("40", 40.0, id="number"),
        pytest.param("uncapped", None, id="uncapped"),
    ],
)
def test_the_env_overlay_sets_the_budget_with_env_provenance(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
    expected: float | None,
) -> None:
    """``LINTRO_AI_REVIEW_PR_BUDGET_USD`` overlays the config, source ENV.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        raw: The variable's value.
        expected: The resolved budget.
    """
    monkeypatch.setenv(ENV_REVIEW_PR_BUDGET_USD, raw)

    resolved = AIConfig.resolve_from_mapping({"review_pr_budget_usd": 10.0})

    assert_that(resolved.config.review_pr_budget_usd).is_equal_to(expected)
    assert_that(resolved.source_of("review_pr_budget_usd")).is_equal_to(
        ConfigSource.ENV,
    )


@pytest.mark.parametrize("raw", ["0", "-1", "lots"], ids=["zero", "negative", "text"])
def test_the_env_overlay_rejects_an_ambiguous_or_bad_value(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
) -> None:
    """``0`` is rejected like the round cap's overlay; so is garbage.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        raw: The variable's value.
    """
    monkeypatch.setenv(ENV_REVIEW_PR_BUDGET_USD, raw)

    with pytest.raises(AIConfigOverrideError, match=ENV_REVIEW_PR_BUDGET_USD):
        AIConfig.resolve_from_mapping({})


def test_a_yaml_budget_has_config_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the overlay the committed budget is CONFIG, not enforced on CLI.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.delenv(ENV_REVIEW_PR_BUDGET_USD, raising=False)

    resolved = AIConfig.resolve_from_mapping({"review_pr_budget_usd": 50.0})

    assert_that(resolved.config.review_pr_budget_usd).is_equal_to(50.0)
    assert_that(resolved.source_of("review_pr_budget_usd")).is_equal_to(
        ConfigSource.CONFIG,
    )


# --- The stop inside a real run -------------------------------------------


def _provider() -> MagicMock:
    """Return a provider double whose every call costs $0.01.

    Returns:
        The provider double.
    """
    provider = MagicMock()
    provider.aclose = AsyncMock()
    provider.model_name = "claude-sonnet-4-20250514"
    provider.name = "anthropic"
    provider.capabilities = ProviderCapabilities(supports_sessions=False)
    provider.complete.return_value = AIResponse(
        content=json.dumps({"summary": "ok", "findings": []}),
        model="claude-sonnet-4-20250514",
        input_tokens=100,
        output_tokens=50,
        cost_estimate=0.01,
        provider="anthropic",
    )
    return provider


def _chunks() -> list[ReviewChunk]:
    """Return two single-file chunks.

    Returns:
        The chunks.
    """
    return [
        ReviewChunk(
            id=1,
            files=["a.py"],
            diff="diff --git a/a.py b/a.py\n+x",
            relationship=REL_SINGLE_FILE,
        ),
        ReviewChunk(
            id=2,
            files=["b.py"],
            diff="diff --git a/b.py b/b.py\n+y",
            relationship=REL_SINGLE_FILE,
        ),
    ]


def _context() -> ReviewContext:
    """Return a two-file review context matching :func:`_chunks`.

    Returns:
        The context.
    """
    return ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(path="a.py", status="modified", additions=1, deletions=0),
            ChangedFile(path="b.py", status="modified", additions=1, deletions=0),
        ],
        unified_diff="diff --git a/a.py b/a.py\n+x\ndiff --git a/b.py b/b.py\n+y",
        pr_metadata=None,
    )


def _review(*, provider: MagicMock, pr_budget: PrBudget | None) -> ReviewResult:
    """Run a two-chunk review under ``pr_budget`` with a charging call path.

    Args:
        provider: The provider double.
        pr_budget: The PR budget for the round.

    Returns:
        The review result.
    """

    def _charging_call_ai(
        *,
        provider: MagicMock,
        user_prompt: str,
        budget: Any = None,
        **kwargs: Any,
    ) -> AIResponse:
        # No budget.check() here: the stop must come from the run's own
        # check points, not from this double.
        response: AIResponse = provider.complete(
            user_prompt,
            system=kwargs.get("system_prompt"),
            max_tokens=kwargs.get("max_tokens", 1024),
        )
        if budget is not None:
            budget.record(response.cost_estimate)
        return response

    with (
        patch(
            "lintro.ai.review.run_planning.resolve_review_chunks",
            return_value=_chunks(),
        ),
        patch(
            "lintro.ai.review.provider_call.call_ai",
            side_effect=_charging_call_ai,
        ),
    ):
        return run_review(
            _context(),
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(
                    enabled=True,
                    transport=AITransport.API,
                    max_parallel_calls=1,
                ),
                depth=1,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
                pr_budget=pr_budget,
            ),
        )


def test_a_spent_budget_stops_the_round_before_any_provider_call() -> None:
    """Spend at or over the budget: no call, a graceful ``PR budget`` stop."""
    provider = _provider()

    result = _review(
        provider=provider,
        pr_budget=PrBudget(budget_usd=40.0, prior_spend_usd=40.0, enforced=True),
    )

    provider.complete.assert_not_called()
    assert_that(result.metadata.partial).is_true()
    assert_that(result.metadata.stopped_reason).is_equal_to(
        "PR budget ($40.00) reached",
    )
    assert_that(result.metadata.chunks_reviewed).is_equal_to(0)


def test_a_budget_crossed_mid_round_stops_at_the_next_check() -> None:
    """The call in flight finishes; the next chunk does not start."""
    provider = _provider()

    result = _review(
        provider=provider,
        pr_budget=PrBudget(budget_usd=10.0, prior_spend_usd=9.99, enforced=True),
    )

    assert_that(result.metadata.partial).is_true()
    assert_that(result.metadata.stopped_reason).starts_with(PR_BUDGET_REASON_PREFIX)
    assert_that(result.metadata.chunks_reviewed).is_equal_to(1)
    assert_that(result.metadata.chunks_total).is_equal_to(2)


def test_a_real_runs_checkpoints_carry_its_spend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each mid-run checkpoint stores what the round has spent so far.

    Two chunks at $0.01 each; with no final write (the CLI's, skipped here),
    the checkpointed state alone must already hold the round's $0.02.

    Args:
        tmp_path: The state directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("LINTRO_REVIEW_STATE_DIR", str(tmp_path))

    _review(provider=_provider(), pr_budget=None)
    state = load_ci_state(directory=tmp_path, repo="", pr_number=0)

    assert_that(state.review_spend_usd).is_close_to(0.02, 1e-9)


@pytest.mark.parametrize(
    "pr_budget",
    [
        pytest.param(None, id="unset"),
        pytest.param(
            PrBudget(budget_usd=1.0, prior_spend_usd=5.0, enforced=False),
            id="display-only",
        ),
    ],
)
def test_an_unset_or_display_only_budget_never_stops_a_round(
    pr_budget: PrBudget | None,
) -> None:
    """Without an enforced budget the round runs to the end.

    Args:
        pr_budget: No budget, or one the cost basis does not enforce.
    """
    result = _review(provider=_provider(), pr_budget=pr_budget)

    assert_that(result.metadata.partial).is_false()
    assert_that(result.metadata.chunks_reviewed).is_equal_to(2)


# --- The sticky line ------------------------------------------------------


def _sticky(result: ReviewResult) -> str:
    """Render the sticky's *This run* section for ``result``.

    Args:
        result: The review result.

    Returns:
        The section text.
    """
    return _this_run_section(result=result, transport="api", auth_mode="api_key")


def test_the_sticky_shows_the_prs_spend_against_its_budget() -> None:
    """``PR budget: $X of $Y`` with X including this round."""
    result = _review(provider=_provider(), pr_budget=None)
    metadata = replace(
        result.metadata,
        pr_budget_usd=40.0,
        pr_budget_spent_usd=17.66,
        pr_budget_enforced=True,
    )
    section = _sticky(replace(result, metadata=metadata))

    assert_that(section).contains("PR budget: $17.66 of $40.00")
    assert_that(section).does_not_contain("display only")
    assert_that(section).does_not_contain("runtime bound")


def test_the_sticky_notes_the_runtime_bound_on_the_cli_basis() -> None:
    """An enforced budget on an unpriceable basis says what the dollars are."""
    result = _review(provider=_provider(), pr_budget=None)
    metadata = replace(
        result.metadata,
        pr_budget_usd=40.0,
        pr_budget_spent_usd=17.66,
        pr_budget_enforced=True,
        cost_basis="unpriceable",
    )
    section = _sticky(replace(result, metadata=metadata))

    assert_that(section).contains(
        "PR budget: $17.66 of $40.00 (runtime bound on the cli transport)",
    )


def test_the_sticky_marks_a_display_only_budget() -> None:
    """A budget the basis does not enforce says so rather than implying a stop."""
    result = _review(provider=_provider(), pr_budget=None)
    metadata = replace(result.metadata, pr_budget_usd=40.0, pr_budget_spent_usd=3.0)
    section = _sticky(replace(result, metadata=metadata))

    assert_that(section).contains("PR budget: $3.00 of $40.00 (display only")


def test_a_display_only_budget_on_the_cli_basis_carries_both_notes() -> None:
    """The unenforced CLI case (a YAML-only budget) names both facts once."""
    result = _review(provider=_provider(), pr_budget=None)
    metadata = replace(
        result.metadata,
        pr_budget_usd=40.0,
        pr_budget_spent_usd=3.0,
        cost_basis="unpriceable",
    )
    section = _sticky(replace(result, metadata=metadata))

    assert_that(section).contains(
        "PR budget: $3.00 of $40.00 (display only: not enforced on this cost "
        "basis; runtime bound on the cli transport)",
    )
    assert_that(section.count("runtime bound")).is_equal_to(1)


def test_the_cli_stamps_spend_including_this_round() -> None:
    """The stamped spend is prior rounds plus this round's cost."""
    # The command's own path needs a live PR (gh, prior state, posting), so
    # the stamp helper it calls is the boundary tested here, deliberately.
    from lintro.cli_utils.commands.review import _pr_budget_fields

    result = _review(provider=_provider(), pr_budget=None)
    this_round = result.metadata.cost_estimate_usd
    budget = PrBudget(budget_usd=40.0, prior_spend_usd=17.66, enforced=True)

    fields = _pr_budget_fields(result=result, pr_budget=budget)

    assert_that(fields["pr_budget_usd"]).is_equal_to(40.0)
    assert_that(fields["pr_budget_spent_usd"]).is_close_to(17.66 + this_round, 1e-9)
    assert_that(fields["pr_budget_enforced"]).is_true()
    assert_that(_pr_budget_fields(result=result, pr_budget=None)).is_empty()


def test_an_unset_budget_has_no_sticky_line() -> None:
    """No budget configured, no line."""
    result = _review(provider=_provider(), pr_budget=None)

    assert_that(_sticky(result)).does_not_contain("PR budget")
