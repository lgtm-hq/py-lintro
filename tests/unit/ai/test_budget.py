"""Tests for the CostBudget session tracker."""

from __future__ import annotations

import threading

import pytest
from assertpy import assert_that

from lintro.ai.budget import CostBudget
from lintro.ai.exceptions import AIError

# -- Defaults ----------------------------------------------------------------


def test_default_budget_has_no_limit() -> None:
    """A budget with no max_cost_usd has unlimited remaining."""
    budget = CostBudget()
    assert_that(budget.max_cost_usd).is_none()
    assert_that(budget.spent).is_equal_to(0.0)
    assert_that(budget.remaining).is_none()


def test_budget_with_limit() -> None:
    """A budget with max_cost_usd reports remaining correctly."""
    budget = CostBudget(max_cost_usd=5.0)
    assert_that(budget.max_cost_usd).is_equal_to(5.0)
    assert_that(budget.remaining).is_equal_to(5.0)


# -- Record ------------------------------------------------------------------


def test_record_increments_spent() -> None:
    """Recording cost increments the spent total."""
    budget = CostBudget(max_cost_usd=10.0)
    budget.record(1.5)
    assert_that(budget.spent).is_equal_to(1.5)
    budget.record(2.0)
    assert_that(budget.spent).is_equal_to(3.5)


def test_record_updates_remaining() -> None:
    """Recording cost decreases remaining budget."""
    budget = CostBudget(max_cost_usd=5.0)
    budget.record(3.0)
    assert_that(budget.remaining).is_equal_to(2.0)


def test_remaining_never_negative() -> None:
    """Remaining is clamped to 0.0 when overspent."""
    budget = CostBudget(max_cost_usd=1.0)
    budget.record(2.0)
    assert_that(budget.remaining).is_equal_to(0.0)


# -- Check -------------------------------------------------------------------


def test_check_passes_when_under_limit() -> None:
    """check() does not raise when spent is below the limit."""
    budget = CostBudget(max_cost_usd=5.0)
    budget.record(2.0)
    budget.check()  # should not raise


def test_check_passes_with_no_limit() -> None:
    """check() never raises when max_cost_usd is None."""
    budget = CostBudget()
    budget.record(1000.0)
    budget.check()  # should not raise


def test_check_raises_when_at_limit() -> None:
    """check() raises AIError when spent equals the limit."""
    budget = CostBudget(max_cost_usd=2.0)
    budget.record(2.0)
    with pytest.raises(AIError, match="cost budget exceeded"):
        budget.check()


def test_check_raises_when_over_limit() -> None:
    """check() raises AIError when spent exceeds the limit."""
    budget = CostBudget(max_cost_usd=1.0)
    budget.record(1.5)
    with pytest.raises(AIError, match="cost budget exceeded"):
        budget.check()


def test_check_error_message_contains_amounts() -> None:
    """The AIError message includes both spent and limit amounts."""
    budget = CostBudget(max_cost_usd=2.0)
    budget.record(2.5)
    with pytest.raises(AIError, match=r"\$2\.5000 spent.*\$2\.00"):
        budget.check()


# -- Thread safety -----------------------------------------------------------


def test_thread_safety_concurrent_records() -> None:
    """Concurrent record() calls produce correct total."""
    budget = CostBudget(max_cost_usd=None)
    num_threads = 10
    increments_per_thread = 100
    cost_per_increment = 0.01

    def worker() -> None:
        for _ in range(increments_per_thread):
            budget.record(cost_per_increment)

    threads = [threading.Thread(target=worker) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    expected = num_threads * increments_per_thread * cost_per_increment
    assert_that(budget.spent).is_close_to(expected, tolerance=1e-9)
