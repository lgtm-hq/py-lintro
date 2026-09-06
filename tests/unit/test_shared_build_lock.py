"""Ownership rules for the shared ``uv build`` lock in the root conftest.

The lock exists so only one worker runs ``uv build`` at a time; a wheel built
by two overlapping runs is silently missing packages. Reclaiming a lock whose
owner died is therefore only safe when it cannot delete a live successor's
lock, which is the branch these tests pin (#2375).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from assertpy import assert_that

from tests.conftest import _reclaim_stale_lock, _release_lock


def _dead_pid() -> int:
    """Find a pid that no process holds.

    Returns:
        A pid for which ``os.kill(pid, 0)`` raises ``ProcessLookupError``.

    Raises:
        RuntimeError: If no unused pid could be found.
    """
    for candidate in range(4_000_000, 4_000_100):
        try:
            os.kill(candidate, 0)
        except ProcessLookupError:
            return candidate
        except (PermissionError, OverflowError, OSError):
            continue
    raise RuntimeError("no unused pid available for the test")


def test_reclaim_removes_a_lock_whose_owner_is_gone(tmp_path: Path) -> None:
    """A lock left behind by a dead worker is reclaimed.

    Args:
        tmp_path: Pytest temporary directory holding the lock file.
    """
    lock = tmp_path / "lintro-dist.lock"
    lock.write_text(f"{_dead_pid()}:abc123", encoding="utf-8")

    reclaimed = _reclaim_stale_lock(lock=lock)

    assert_that(reclaimed).is_true()
    assert_that(lock.exists()).is_false()


def test_reclaim_leaves_a_lock_held_by_a_live_owner(tmp_path: Path) -> None:
    """A live owner keeps its lock however long the build takes.

    Args:
        tmp_path: Pytest temporary directory holding the lock file.
    """
    lock = tmp_path / "lintro-dist.lock"
    token = f"{os.getpid()}:abc123"
    lock.write_text(token, encoding="utf-8")

    reclaimed = _reclaim_stale_lock(lock=lock)

    assert_that(reclaimed).is_false()
    assert_that(lock.read_text(encoding="utf-8")).is_equal_to(token)


def test_reclaim_leaves_a_successor_lock_taken_after_the_liveness_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successor that grabs the lock mid-reclaim keeps it.

    Between reading the dead owner's token and unlinking, another worker can
    create the lock afresh. Deleting it there would let two ``uv build`` runs
    overlap, so the token is compared once more before the unlink.

    Args:
        tmp_path: Pytest temporary directory holding the lock file.
        monkeypatch: Pytest monkeypatch fixture, used to swap the token
            between the liveness check and the unlink.
    """
    lock = tmp_path / "lintro-dist.lock"
    stale_token = f"{_dead_pid()}:abc123"
    successor_token = f"{os.getpid()}:def456"
    lock.write_text(stale_token, encoding="utf-8")

    tokens = iter([stale_token, successor_token])

    def _read(lock_path: Path) -> str:
        """Return the next scripted token, landing the successor on disk.

        Writing the successor's bytes as the stale token is handed out makes
        the lock file itself hold what a real successor would have written, so
        the helper's re-read compare is exercised against the filesystem
        rather than against the stub alone.

        Args:
            lock_path: Lock path the reclaim helper is reading.

        Returns:
            The next token in the scripted sequence.
        """
        token = next(tokens)
        if token == stale_token:
            lock_path.write_text(successor_token, encoding="utf-8")
        return token

    monkeypatch.setattr(
        "tests.conftest._read_lock_token",
        lambda *, lock: _read(lock),
    )

    reclaimed = _reclaim_stale_lock(lock=lock)

    assert_that(reclaimed).is_false()
    assert_that(lock.read_text(encoding="utf-8")).is_equal_to(successor_token)


def test_release_only_removes_the_lock_this_worker_still_owns(
    tmp_path: Path,
) -> None:
    """Releasing a reclaimed lock leaves the new owner's file in place.

    Args:
        tmp_path: Pytest temporary directory holding the lock file.
    """
    lock = tmp_path / "lintro-dist.lock"
    lock.write_text("999999:successor", encoding="utf-8")

    released = _release_lock(lock=lock, token=f"{os.getpid()}:mine")

    assert_that(released).is_false()
    assert_that(lock.exists()).is_true()

    lock.write_text(f"{os.getpid()}:mine", encoding="utf-8")

    assert_that(_release_lock(lock=lock, token=f"{os.getpid()}:mine")).is_true()
    assert_that(lock.exists()).is_false()
