"""Unit tests for the shared ARG_MAX-safe argv batching helpers."""

from __future__ import annotations

from unittest.mock import patch

from assertpy import assert_that

from lintro.tools.core.argv_batching import (
    ARGV_FALLBACK_LIMIT_BYTES,
    ARGV_POINTER_BYTES,
    ARGV_SAFETY_HEADROOM_BYTES,
    argv_byte_budget,
    chunk_paths,
)


def test_argv_byte_budget_is_positive() -> None:
    """The derived argv byte budget must be a usable positive number."""
    assert_that(argv_byte_budget()).is_greater_than(0)


def test_argv_byte_budget_falls_back_when_sysconf_fails() -> None:
    """An unavailable ``SC_ARG_MAX`` falls back to the POSIX minimum."""
    with (
        patch(
            "lintro.tools.core.argv_batching.os.sysconf",
            side_effect=OSError("no sysconf"),
        ),
        patch.dict("os.environ", {}, clear=True),
    ):
        budget = argv_byte_budget()

    assert_that(budget).is_equal_to(
        ARGV_FALLBACK_LIMIT_BYTES - ARGV_SAFETY_HEADROOM_BYTES,
    )


def test_argv_byte_budget_never_drops_below_the_headroom_floor() -> None:
    """A huge environment still leaves room for at least one long path."""
    with (
        patch("lintro.tools.core.argv_batching.os.sysconf", return_value=4096),
        patch.dict("os.environ", {"BIG": "x" * 100_000}, clear=True),
    ):
        budget = argv_byte_budget()

    assert_that(budget).is_equal_to(ARGV_SAFETY_HEADROOM_BYTES)


def test_argv_byte_budget_counts_environment_in_bytes_not_characters() -> None:
    """Non-ASCII environment values are charged their encoded byte length."""
    with patch("lintro.tools.core.argv_batching.os.sysconf", return_value=10**7):
        with patch.dict("os.environ", {"K": "a" * 10}, clear=True):
            ascii_budget = argv_byte_budget()
        with patch.dict("os.environ", {"K": "é" * 10}, clear=True):
            latin1_budget = argv_byte_budget()

    # "é" is two bytes in UTF-8, so the same character count costs 10 more.
    assert_that(ascii_budget - latin1_budget).is_equal_to(10)


def test_chunk_paths_returns_no_batches_for_no_paths() -> None:
    """An empty input yields no batches rather than one empty batch."""
    assert_that(chunk_paths([], fixed_arg_bytes=0)).is_equal_to([])


def test_chunk_single_batch_when_budget_fits() -> None:
    """Short paths under the budget stay in a single batch, in order."""
    paths = ["/a/one.py", "/a/two.py", "/a/three.py"]

    batches = chunk_paths(paths, fixed_arg_bytes=0, budget=10_000)

    assert_that(batches).is_length(1)
    assert_that(batches[0]).is_equal_to(paths)


def test_chunk_splits_when_budget_is_exhausted() -> None:
    """Each path lands in its own batch when the budget only fits one."""
    paths = ["/a/one.py", "/a/two.py", "/a/three.py"]
    one_path_budget = len("/a/three.py") + 1 + ARGV_POINTER_BYTES

    batches = chunk_paths(paths, fixed_arg_bytes=0, budget=one_path_budget)

    assert_that(batches).is_length(3)
    assert_that([p for batch in batches for p in batch]).is_equal_to(paths)


def test_chunk_never_drops_an_oversized_path() -> None:
    """A path larger than the whole budget still gets its own batch."""
    huge = "/a/" + ("x" * 5000) + ".py"

    batches = chunk_paths([huge], fixed_arg_bytes=0, budget=10)

    assert_that(batches).is_equal_to([[huge]])


def test_chunk_accounts_for_the_fixed_command_prefix() -> None:
    """Flag bytes are charged against the same budget as the paths."""
    paths = ["/a/one.py", "/a/two.py"]
    budget = sum(len(p) + 1 + ARGV_POINTER_BYTES for p in paths)

    without_prefix = chunk_paths(paths, fixed_arg_bytes=0, budget=budget)
    with_prefix = chunk_paths(paths, fixed_arg_bytes=budget - 1, budget=budget)

    assert_that(without_prefix).is_length(1)
    assert_that(with_prefix).is_length(2)


def test_chunk_preserves_order_across_batches() -> None:
    """Flattening the batches reproduces the input order exactly."""
    paths = [f"/repo/file_{index:04d}.py" for index in range(50)]

    batches = chunk_paths(paths, fixed_arg_bytes=0, budget=64)

    assert_that([p for batch in batches for p in batch]).is_equal_to(paths)


def test_chunk_uses_the_derived_budget_by_default() -> None:
    """Omitting ``budget`` falls back to :func:`argv_byte_budget`."""
    paths = ["/a/one.py", "/a/two.py"]

    with patch(
        "lintro.tools.core.argv_batching.argv_byte_budget",
        return_value=len("/a/one.py") + 1 + ARGV_POINTER_BYTES,
    ):
        batches = chunk_paths(paths, fixed_arg_bytes=0)

    assert_that(batches).is_length(2)
