"""Unit tests for the shared ARG_MAX-safe argv batching helpers."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest
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


@pytest.mark.parametrize(
    "failure",
    [ValueError("bad name"), OSError("no sysconf"), AttributeError("no attr")],
    ids=["ValueError", "OSError", "AttributeError"],
)
def test_argv_byte_budget_falls_back_for_every_sysconf_failure(
    failure: Exception,
) -> None:
    """Each documented ``sysconf`` failure mode reaches the same fallback.

    Args:
        failure: Exception raised by ``os.sysconf``.
    """
    with (
        patch("lintro.tools.core.argv_batching.os.sysconf", side_effect=failure),
        patch.dict("os.environ", {}, clear=True),
    ):
        budget = argv_byte_budget()

    assert_that(budget).is_equal_to(
        ARGV_FALLBACK_LIMIT_BYTES - ARGV_SAFETY_HEADROOM_BYTES,
    )


@pytest.mark.parametrize(
    "arg_max",
    [-1, 0, None],
    ids=["negative", "zero", "not-an-int"],
)
def test_argv_byte_budget_falls_back_for_unusable_arg_max(arg_max: object) -> None:
    """A non-positive or non-int ``SC_ARG_MAX`` uses the POSIX minimum.

    Args:
        arg_max: Value returned by ``os.sysconf``.
    """
    with (
        patch("lintro.tools.core.argv_batching.os.sysconf", return_value=arg_max),
        patch.dict("os.environ", {}, clear=True),
    ):
        budget = argv_byte_budget()

    assert_that(budget).is_equal_to(
        ARGV_FALLBACK_LIMIT_BYTES - ARGV_SAFETY_HEADROOM_BYTES,
    )


def test_argv_byte_budget_never_exceeds_arg_max() -> None:
    """An environment that eats the limit must not yield an unusable budget."""
    arg_max = 512
    with (
        patch("lintro.tools.core.argv_batching.os.sysconf", return_value=arg_max),
        patch.dict("os.environ", {"BIG": "x" * 100_000}, clear=True),
    ):
        budget = argv_byte_budget()

    assert_that(budget).is_greater_than(0)
    assert_that(budget).is_less_than_or_equal_to(arg_max)


def test_argv_byte_budget_uses_the_headroom_floor_when_arg_max_allows() -> None:
    """With a roomy ``ARG_MAX`` the floor is the headroom constant."""
    with (
        patch("lintro.tools.core.argv_batching.os.sysconf", return_value=10**6),
        patch.dict("os.environ", {"BIG": "x" * 10**6}, clear=True),
    ):
        budget = argv_byte_budget()

    assert_that(budget).is_equal_to(ARGV_SAFETY_HEADROOM_BYTES)


def test_argv_byte_budget_counts_environment_in_bytes_not_characters() -> None:
    """Non-ASCII environment values are charged their encoded byte length."""
    plain = "a" * 10
    accented = "é" * 10
    encoding = sys.getfilesystemencoding()
    # Derive the delta the same way production does, so the test does not
    # assume a UTF-8 filesystem encoding.
    expected = len(accented.encode(encoding, "surrogateescape")) - len(
        plain.encode(encoding, "surrogateescape"),
    )

    with patch("lintro.tools.core.argv_batching.os.sysconf", return_value=10**7):
        with patch.dict("os.environ", {"K": plain}, clear=True):
            plain_budget = argv_byte_budget()
        with patch.dict("os.environ", {"K": accented}, clear=True):
            accented_budget = argv_byte_budget()

    assert_that(plain_budget - accented_budget).is_equal_to(expected)


def test_chunk_charges_non_ascii_paths_their_encoded_length() -> None:
    """Path cost is encoding-sensitive, not character-count based."""
    encoding = sys.getfilesystemencoding()
    path = "/café/naïve.md"
    exact_budget = (
        len(path.encode(encoding, "surrogateescape")) + 1 + ARGV_POINTER_BYTES
    )

    fits = chunk_paths([path, path], fixed_arg_bytes=0, budget=exact_budget * 2)
    splits = chunk_paths([path, path], fixed_arg_bytes=0, budget=exact_budget * 2 - 1)

    assert_that(fits).is_length(1)
    assert_that(splits).is_length(2)


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
