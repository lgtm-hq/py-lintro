"""Tests for the watch-mode debouncer.

The debounce timer is injected via a fake factory, so timing behaviour is
exercised deterministically without any ``sleep``.
"""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.watch.debouncer import Debouncer
from tests.unit.watch.conftest import FakeTimerFactory


def test_single_change_fires_batch_on_timer(
    fake_timer_factory: FakeTimerFactory,
) -> None:
    """A single change fires exactly one batch when the timer elapses."""
    batches: list[set[str]] = []
    debouncer = Debouncer(
        callback=batches.append,
        delay_ms=300,
        timer_factory=fake_timer_factory,
    )

    debouncer.on_change("a.py")
    assert_that(batches).is_empty()

    fake_timer_factory.latest.fire()

    assert_that(batches).is_length(1)
    assert_that(batches[0]).is_equal_to({"a.py"})


def test_rapid_changes_coalesce_into_one_batch(
    fake_timer_factory: FakeTimerFactory,
) -> None:
    """Multiple rapid changes collapse into a single batched run."""
    batches: list[set[str]] = []
    debouncer = Debouncer(
        callback=batches.append,
        delay_ms=300,
        timer_factory=fake_timer_factory,
    )

    debouncer.on_change("a.py")
    debouncer.on_change("b.py")
    debouncer.on_change("a.py")  # duplicate collapses in the set

    # Only the final, still-active timer should fire.
    fake_timer_factory.latest.fire()

    assert_that(batches).is_length(1)
    assert_that(batches[0]).is_equal_to({"a.py", "b.py"})


def test_each_change_resets_the_timer(
    fake_timer_factory: FakeTimerFactory,
) -> None:
    """Every new change cancels the prior timer and arms a fresh one."""
    debouncer = Debouncer(
        callback=lambda _batch: None,
        delay_ms=300,
        timer_factory=fake_timer_factory,
    )

    debouncer.on_change("a.py")
    first = fake_timer_factory.latest
    debouncer.on_change("b.py")
    second = fake_timer_factory.latest

    assert_that(first.cancelled).is_true()
    assert_that(second.cancelled).is_false()
    assert_that(fake_timer_factory.timers).is_length(2)


def test_empty_batch_does_not_invoke_callback(
    fake_timer_factory: FakeTimerFactory,
) -> None:
    """Firing with no pending paths must not call the callback."""
    calls: list[set[str]] = []
    debouncer = Debouncer(
        callback=calls.append,
        delay_ms=300,
        timer_factory=fake_timer_factory,
    )

    debouncer.on_change("a.py")
    debouncer.flush()  # drains the batch
    assert_that(calls).is_length(1)

    # Firing the (already-consumed) timer must not re-emit.
    fake_timer_factory.latest.fire()
    assert_that(calls).is_length(1)


def test_flush_emits_pending_immediately(
    fake_timer_factory: FakeTimerFactory,
) -> None:
    """flush() emits the current batch without waiting for the timer."""
    batches: list[set[str]] = []
    debouncer = Debouncer(
        callback=batches.append,
        delay_ms=300,
        timer_factory=fake_timer_factory,
    )

    debouncer.on_change("a.py")
    debouncer.flush()

    assert_that(batches).is_length(1)
    assert_that(batches[0]).is_equal_to({"a.py"})
    assert_that(fake_timer_factory.latest.cancelled).is_true()


def test_cancel_discards_pending_without_firing(
    fake_timer_factory: FakeTimerFactory,
) -> None:
    """cancel() drops queued paths and never invokes the callback."""
    batches: list[set[str]] = []
    debouncer = Debouncer(
        callback=batches.append,
        delay_ms=300,
        timer_factory=fake_timer_factory,
    )

    debouncer.on_change("a.py")
    debouncer.cancel()

    assert_that(batches).is_empty()
    assert_that(debouncer.pending).is_empty()
    assert_that(fake_timer_factory.latest.cancelled).is_true()


def test_pending_property_reflects_queued_paths(
    fake_timer_factory: FakeTimerFactory,
) -> None:
    """The pending snapshot reports queued but not-yet-fired paths."""
    debouncer = Debouncer(
        callback=lambda _batch: None,
        delay_ms=300,
        timer_factory=fake_timer_factory,
    )

    debouncer.on_change("a.py")
    debouncer.on_change("b.py")

    assert_that(debouncer.pending).is_equal_to({"a.py", "b.py"})


def test_negative_delay_is_rejected() -> None:
    """A negative debounce delay raises ValueError."""
    assert_that(Debouncer).raises(ValueError).when_called_with(
        callback=lambda _batch: None,
        delay_ms=-1,
    )


def test_reuse_after_fire_starts_clean(
    fake_timer_factory: FakeTimerFactory,
) -> None:
    """After a batch fires, a subsequent change starts a fresh batch."""
    batches: list[set[str]] = []
    debouncer = Debouncer(
        callback=batches.append,
        delay_ms=300,
        timer_factory=fake_timer_factory,
    )

    debouncer.on_change("a.py")
    fake_timer_factory.latest.fire()

    debouncer.on_change("b.py")
    fake_timer_factory.latest.fire()

    assert_that(batches).is_length(2)
    assert_that(batches[0]).is_equal_to({"a.py"})
    assert_that(batches[1]).is_equal_to({"b.py"})


@pytest.mark.parametrize("delay_ms", [0, 1, 300, 1000])
def test_timer_receives_delay_in_seconds(
    delay_ms: int,
    fake_timer_factory: FakeTimerFactory,
) -> None:
    """The factory is handed the delay converted to seconds."""
    debouncer = Debouncer(
        callback=lambda _batch: None,
        delay_ms=delay_ms,
        timer_factory=fake_timer_factory,
    )

    debouncer.on_change("a.py")

    assert_that(fake_timer_factory.latest.delay).is_equal_to(delay_ms / 1000.0)
