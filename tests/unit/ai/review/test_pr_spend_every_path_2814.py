"""The PR spend total holds on every state path (#2814, rulings 19-20).

#2811 made the per-PR review spend cumulative. Four paths still let it drift:

* a call charged to the round's budget whose result was dropped (a chunk
  cancelled at a budget stop) was missing from the round's cost, so the
  sticky's ``PR budget: $X of $Y`` understated what the next round enforces;
* pruning a sticky blob rebuilt the state without the total;
* two predicates decided "which PR" in ``lifecycle/state.py``;
* a state part with an empty repo or PR matched any caller.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.enums.config_source import ConfigSource
from lintro.ai.enums.cost_basis import CostBasis
from lintro.ai.exceptions import AICostBudgetExceededError
from lintro.ai.providers.response import AIResponse
from lintro.ai.review.lifecycle.state import (
    load_prior_review_state,
    persist_review_state,
)
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.models.run_usage import RunUsage
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.orchestrator import run_review
from lintro.ai.review.pr_budget import PrBudget, resolve_pr_budget
from lintro.ai.review.review_state_codec import (
    decode_state,
    leftover_state_block,
    prune_state_to_fit,
)
from lintro.ai.review.session import ReviewSessionOptions
from lintro.ai.review.state_store import load_ci_state, write_state_part
from lintro.ai.review.sticky.assembly import advance_review_state
from lintro.ai.review.sticky.history import _this_run_section
from lintro.cli_utils.commands.review import _pr_budget_fields
from tests.unit.ai.review.test_pr_budget_2796 import (
    _chunks,
    _context,
    _provider,
    _review,
)
from tests.unit.ai.review.test_pr_spend_2796 import _context as _spend_context
from tests.unit.ai.review.test_pr_spend_2796 import _result

_PRIOR = 9.0


def _review_with_a_dropped_charge() -> tuple[ReviewResult, float]:
    """Run two chunks where the second is charged, then stopped at the budget.

    The second call settles its cost on the round's budget and then raises the
    cost-cap stop, as a parallel chunk cancelled at the budget does: the
    charge is real, but no result is kept for it.

    Returns:
        The review result and the budget's final ``spent``.
    """
    calls = {"n": 0}
    spent: dict[str, float] = {}

    def _call_ai(
        *,
        provider: Any,
        user_prompt: str,
        budget: Any = None,
        **kwargs: Any,
    ) -> AIResponse:
        calls["n"] += 1
        response: AIResponse = provider.complete(
            user_prompt,
            system=kwargs.get("system_prompt"),
            max_tokens=kwargs.get("max_tokens", 1024),
        )
        if budget is not None:
            budget.record(response.cost_estimate)
            spent["value"] = budget.spent
        if calls["n"] == 2:
            raise AICostBudgetExceededError("stopped at the PR budget")
        return response

    with (
        patch(
            "lintro.ai.review.run_planning.resolve_review_chunks",
            return_value=_chunks(),
        ),
        patch("lintro.ai.review.provider_call.call_ai", side_effect=_call_ai),
    ):
        result = run_review(
            _context(),
            options=ReviewSessionOptions(
                provider=_provider(),
                ai_config=AIConfig(
                    enabled=True,
                    transport=AITransport.API,
                    max_parallel_calls=1,
                ),
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
                pr_budget=PrBudget(
                    budget_usd=10.0,
                    prior_spend_usd=_PRIOR,
                    enforced=True,
                ),
            ),
        )
    return result, spent["value"]


def test_a_charged_but_dropped_call_is_recorded_everywhere() -> None:
    """Ruling 19: the round's cost, the stamp, the state and the sticky agree.

    The sticky's figure equals the spend the next round enforces against.
    """
    result, spent = _review_with_a_dropped_charge()
    prior_state = ReviewState(runs=(RunRecord(usage=RunUsage(cost=_PRIOR)),))
    budget = PrBudget(budget_usd=10.0, prior_spend_usd=_PRIOR, enforced=True)

    stamped = replace(
        result,
        metadata=replace(
            result.metadata,
            **_pr_budget_fields(result=result, pr_budget=budget),
        ),
    )
    persisted = advance_review_state(
        request=StickyRequest(result=stamped, prior_state=prior_state),
    )
    next_round = resolve_pr_budget(
        budget_usd=10.0,
        source=ConfigSource.ENV,
        basis=CostBasis.BILLED,
        prior_state=persisted,
    )
    sticky = _this_run_section(result=stamped, transport="api", auth_mode="api_key")

    assert_that(spent).is_close_to(0.02, 1e-9)
    assert_that(result.metadata.partial).is_true()
    assert_that(result.metadata.cost_estimate_usd).is_close_to(spent, 1e-9)
    assert_that(stamped.metadata.pr_budget_spent_usd).is_close_to(_PRIOR + spent, 1e-9)
    assert_that(persisted.review_spend_usd).is_close_to(_PRIOR + spent, 1e-9)
    assert next_round is not None
    assert_that(next_round.prior_spend_usd).is_close_to(
        stamped.metadata.pr_budget_spent_usd,
        1e-9,
    )
    assert_that(sticky).contains(f"PR budget: ${_PRIOR + spent:.2f} of $10.00")


def test_a_round_that_kept_every_call_costs_the_result_sum() -> None:
    """With nothing dropped, the round's cost is exactly its results' sum."""
    result = _review(provider=_provider(), pr_budget=None)

    assert_that(result.metadata.partial).is_false()
    assert_that(result.metadata.cost_estimate_usd).is_close_to(0.02, 1e-9)


# --- Item 1: pruning keeps the total ---------------------------------------


def test_a_pruned_sticky_blob_keeps_the_cumulative_total() -> None:
    """``prune_state_to_fit`` keeps ``pr_spend_usd`` down to a single run."""
    runs = tuple(RunRecord(usage=RunUsage(cost=1.0)) for _ in range(20))
    state = ReviewState(runs=runs, pr_spend_usd=250.0)
    body = "x" * 100

    full = len(body) + len(leftover_state_block(state=state))
    pruned = prune_state_to_fit(state=state, body=body, limit=full - 50)
    decoded = decode_state(body=leftover_state_block(state=pruned))

    assert_that(len(pruned.runs)).is_less_than(len(runs))
    assert_that(pruned.truncated).is_true()
    assert_that(decoded.review_spend_usd).is_close_to(250.0, 1e-9)


def test_pruning_to_one_run_still_keeps_the_total() -> None:
    """Even the smallest reachable blob carries the total."""
    runs = tuple(RunRecord(usage=RunUsage(cost=1.0)) for _ in range(5))
    state = ReviewState(runs=runs, pr_spend_usd=99.0)

    pruned = prune_state_to_fit(state=state, body="", limit=1)

    assert_that(pruned.runs).is_length(1)
    assert_that(pruned.review_spend_usd).is_close_to(99.0, 1e-9)


# --- Item 2: one "which PR" predicate ---------------------------------------


def test_pruning_a_state_without_a_stored_total_keeps_its_run_spend() -> None:
    """A legacy state (spend from its runs only) keeps that spend when pruned."""
    runs = tuple(RunRecord(usage=RunUsage(cost=2.0)) for _ in range(6))
    state = ReviewState(runs=runs)

    pruned = prune_state_to_fit(state=state, body="", limit=1)

    assert_that(state.pr_spend_usd).is_equal_to(0.0)
    assert_that(pruned.runs).is_length(1)
    assert_that(pruned.review_spend_usd).is_close_to(12.0, 1e-9)


# --- Item 2: one "which PR" predicate ---------------------------------------


@pytest.mark.parametrize("pr_number", [None, 0], ids=["none", "zero"])
def test_no_pr_means_the_same_to_every_ci_state_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pr_number: int | None,
) -> None:
    """``None`` and ``0`` both mean "no PR" to the load and the final write.

    A PR's part in the state directory must neither load as prior state nor
    raise the final write's total when the round has no PR.

    Args:
        tmp_path: Scratch directory used as the CI state directory.
        monkeypatch: Pytest monkeypatch fixture.
        pr_number: The caller's "no PR" spelling.
    """
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("LINTRO_REVIEW_STATE_DIR", str(tmp_path))
    write_state_part(
        state=ReviewState(repo="o/r", pr_number=2814, pr_spend_usd=77.0),
        directory=tmp_path,
        sequence=2,
    )

    prior = load_prior_review_state(pr_number=pr_number, head_ref="head", repo="o/r")
    persist_review_state(
        result=_result(1.0),
        context=_spend_context(),
        prior=prior,
        pr_number=pr_number,
        repo="o/r",
    )
    written = load_ci_state(directory=tmp_path, repo="o/r", pr_number=0)

    assert_that(prior.review_spend_usd).is_equal_to(0.0)
    assert_that(written.review_spend_usd).is_close_to(1.0, 1e-9)


# --- Item 3: empty identities never match a named caller --------------------


def _part(directory: Path, name: str, payload_update: dict[str, Any]) -> None:
    """Write one state part with its identity fields overridden.

    Args:
        directory: The state directory.
        name: The part's file name.
        payload_update: Payload keys to override.
    """
    payload = ReviewState(pr_spend_usd=500.0).to_artifact_dict()
    payload.update(payload_update)
    (directory / name).write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.parametrize(
    "identity",
    [
        pytest.param({"repo": "", "pr_number": 2814}, id="empty-repo"),
        pytest.param({"repo": "o/r", "pr_number": None}, id="no-pr"),
        pytest.param({"repo": "o/r", "pr_number": ""}, id="empty-pr"),
        pytest.param({"repo": "o/r", "pr_number": "abc"}, id="bad-pr"),
        pytest.param({"repo": "x/y", "pr_number": 2814}, id="other-repo"),
        pytest.param({"repo": "o/r", "pr_number": 1}, id="other-pr"),
    ],
)
def test_a_part_without_this_prs_identity_never_contributes(
    tmp_path: Path,
    identity: dict[str, Any],
) -> None:
    """Neither coverage nor spend comes from a part that is not this PR's.

    Args:
        tmp_path: Scratch state directory.
        identity: The part's stored repo and PR.
    """
    _part(tmp_path, "part-0001.json", identity)

    loaded = load_ci_state(directory=tmp_path, repo="o/r", pr_number=2814)

    assert_that(loaded.review_spend_usd).is_equal_to(0.0)


def test_this_prs_checkpoint_is_still_loaded(tmp_path: Path) -> None:
    """A checkpoint stamped as CI stamps it (repo and ``PR_NUMBER``) loads.

    Args:
        tmp_path: Scratch state directory.
    """
    _part(tmp_path, "part-0001.json", {"repo": "o/r", "pr_number": 2814})
    _part(tmp_path, "part-0002.json", {"repo": "o/r", "pr_number": "2814"})

    loaded = load_ci_state(directory=tmp_path, repo="o/r", pr_number=2814)

    assert_that(loaded.review_spend_usd).is_equal_to(500.0)


def test_a_no_pr_load_still_reads_parts_without_a_pr(tmp_path: Path) -> None:
    """``pr_number=0`` (no PR) keeps matching parts that name none.

    Args:
        tmp_path: Scratch state directory.
    """
    _part(tmp_path, "part-0001.json", {"repo": "", "pr_number": None})

    loaded = load_ci_state(directory=tmp_path, repo="", pr_number=0)

    assert_that(loaded.review_spend_usd).is_equal_to(500.0)
