"""Tests for the watch-mode debouncer.

The debounce timer is injected via a fake factory, so timing behaviour is
exercised deterministically without any ``sleep``.
"""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.config.watch_config import DEFAULT_DEBOUNCE_MS
from lintro.watch.debouncer import Debouncer
from tests.unit.watch.conftest import FakeTimerFactory


def test_single_change_fires_batch_on_timer(
    fake_timer_factory: FakeTimerFactory,
) -> None:
    """A single change fires exactly one batch when the timer elapses."""
    batches: list[set[str]] = []
    debouncer = Debouncer(
        callback=batches.append,
        delay_ms=DEFAULT_DEBOUNCE_MS,
        timer_factory=fake_timer_factory,
    )

    debouncer.on_change("a.py")
    assert_that(batches).is_empty()
    assert_that(fake_timer_factory.latest.started).is_true()

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


def test_cancelled_timer_cannot_drain_replacement_batch(
    fake_timer_factory: FakeTimerFactory,
) -> None:
    """A stale timer firing late cannot consume the active timer's batch."""
    batches: list[set[str]] = []
    debouncer = Debouncer(
        callback=batches.append,
        delay_ms=300,
        timer_factory=fake_timer_factory,
    )

    debouncer.on_change("a.py")
    stale = fake_timer_factory.latest
    debouncer.on_change("b.py")
    active = fake_timer_factory.latest

    stale.fire()
    assert_that(batches).is_empty()
    assert_that(debouncer.pending).is_equal_to({"a.py", "b.py"})

    active.fire()
    assert_that(batches).is_equal_to([{"a.py", "b.py"}])


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


def test_overlapping_fires_are_serialized(
    fake_timer_factory: FakeTimerFactory,
) -> None:
    """A second batch waits until the first callback returns."""
    import threading

    started = threading.Event()
    release = threading.Event()
    order: list[str] = []

    def _slow(batch: set[str]) -> None:
        order.append(f"start:{sorted(batch)[0]}")
        started.set()
        release.wait(timeout=2)
        order.append(f"end:{sorted(batch)[0]}")

    debouncer = Debouncer(
        callback=_slow,
        delay_ms=300,
        timer_factory=fake_timer_factory,
    )

    debouncer.on_change("a.py")
    first = fake_timer_factory.latest
    worker = threading.Thread(target=first.fire)
    worker.start()
    assert_that(started.wait(timeout=2)).is_true()

    debouncer.on_change("b.py")
    second = fake_timer_factory.latest
    second_worker = threading.Thread(target=second.fire)
    second_worker.start()
    # The second callback must not start while the first still holds the lock.
    assert_that(order).is_equal_to(["start:a.py"])

    release.set()
    worker.join(timeout=2)
    second_worker.join(timeout=2)

    assert_that(order).is_equal_to(
        ["start:a.py", "end:a.py", "start:b.py", "end:b.py"],
    )


def test_flush_waits_for_active_callback(
    fake_timer_factory: FakeTimerFactory,
) -> None:
    """An empty flush does not return while a callback remains in progress."""
    import threading

    callback_started = threading.Event()
    release_callback = threading.Event()
    flush_started = threading.Event()
    flush_done = threading.Event()

    def _slow(_batch: set[str]) -> None:
        callback_started.set()
        release_callback.wait(timeout=2)

    debouncer = Debouncer(
        callback=_slow,
        delay_ms=300,
        timer_factory=fake_timer_factory,
    )
    debouncer.on_change("a.py")

    callback_worker = threading.Thread(target=fake_timer_factory.latest.fire)
    callback_worker.start()
    assert_that(callback_started.wait(timeout=2)).is_true()

    def _flush() -> None:
        flush_started.set()
        debouncer.flush()
        flush_done.set()

    flush_worker = threading.Thread(target=_flush)
    flush_worker.start()
    assert_that(flush_started.wait(timeout=2)).is_true()
    assert_that(flush_done.wait(timeout=0.1)).is_false()

    release_callback.set()
    callback_worker.join(timeout=2)
    flush_worker.join(timeout=2)
    assert_that(flush_done.is_set()).is_true()


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
