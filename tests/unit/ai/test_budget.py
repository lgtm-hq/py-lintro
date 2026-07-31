"""Tests for the CostBudget session tracker."""

from __future__ import annotations

import asyncio
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


async def test_execute_runs_budgeted_calls_concurrently() -> None:
    """execute() does not hold a lock across the provider await.

    Regression test for the budget gate serializing every provider call:
    a call blocked mid-await must not prevent a second call from starting.
    """
    budget = CostBudget(max_cost_usd=10.0)
    first_started = asyncio.Event()
    second_started = asyncio.Event()

    async def first_call() -> float:
        """Block until the second call has started.

        Returns:
            The cost attributed to this call.
        """
        first_started.set()
        await asyncio.wait_for(second_started.wait(), timeout=5.0)
        return 0.01

    async def second_call() -> float:
        """Signal that the second budgeted call entered the provider await.

        Returns:
            The cost attributed to this call.
        """
        await first_started.wait()
        second_started.set()
        return 0.02

    costs = await asyncio.gather(
        budget.execute(first_call, cost_of=lambda cost: cost),
        budget.execute(second_call, cost_of=lambda cost: cost),
    )

    assert_that(costs).is_equal_to([0.01, 0.02])
    assert_that(budget.spent).is_close_to(0.03, tolerance=1e-9)


async def test_execute_records_actual_cost() -> None:
    """execute() charges the budget with the cost derived from the result."""
    budget = CostBudget(max_cost_usd=10.0)

    async def call() -> float:
        """Return a fixed cost.

        Returns:
            The cost attributed to this call.
        """
        return 0.25

    await budget.execute(call, cost_of=lambda cost: cost)

    assert_that(budget.spent).is_close_to(0.25, tolerance=1e-9)
    assert_that(budget.reserved).is_equal_to(0.0)


async def test_execute_raises_when_budget_exhausted() -> None:
    """execute() rejects a call that starts once the ceiling is reached."""
    budget = CostBudget(max_cost_usd=1.0)
    budget.record(1.0)
    called = False

    async def call() -> float:
        """Record that the provider was reached.

        Returns:
            The cost attributed to this call.
        """
        nonlocal called
        called = True
        return 0.0

    with pytest.raises(AIError, match="cost budget exceeded"):
        await budget.execute(call, cost_of=lambda cost: cost)

    assert_that(called).is_false()


async def test_execute_raises_when_reservations_fill_the_ceiling() -> None:
    """In-flight reservations block a call that would overshoot the ceiling."""
    budget = CostBudget(max_cost_usd=1.0)
    in_flight = asyncio.Event()
    release = asyncio.Event()

    async def holder() -> float:
        """Hold a reservation until released.

        Returns:
            The cost attributed to this call.
        """
        in_flight.set()
        await release.wait()
        return 0.1

    task = asyncio.create_task(
        budget.execute(holder, cost_of=lambda cost: cost, estimate=1.0),
    )
    await asyncio.wait_for(in_flight.wait(), timeout=5.0)

    async def blocked() -> float:
        """Never reached while the reservation stands.

        Returns:
            The cost attributed to this call.
        """
        return 0.0

    with pytest.raises(AIError, match="cost budget exceeded"):
        await budget.execute(blocked, cost_of=lambda cost: cost)

    release.set()
    await task
    assert_that(budget.reserved).is_equal_to(0.0)
    assert_that(budget.spent).is_close_to(0.1, tolerance=1e-9)


async def test_execute_releases_reservation_on_failure() -> None:
    """A failed call releases its reservation instead of consuming budget."""
    budget = CostBudget(max_cost_usd=1.0)

    async def failing_call() -> float:
        """Fail before returning a cost.

        Returns:
            Never returns normally.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError("provider blew up")

    with pytest.raises(RuntimeError, match="provider blew up"):
        await budget.execute(
            failing_call,
            cost_of=lambda cost: cost,
            estimate=1.0,
        )

    assert_that(budget.reserved).is_equal_to(0.0)
    assert_that(budget.spent).is_equal_to(0.0)
    assert_that(budget.remaining).is_equal_to(1.0)

    async def call() -> float:
        """Succeed after the failed call released its reservation.

        Returns:
            The cost attributed to this call.
        """
        return 0.5

    await budget.execute(call, cost_of=lambda cost: cost)
    assert_that(budget.spent).is_close_to(0.5, tolerance=1e-9)
