"""Tests for collect_streaming_results function."""

from __future__ import annotations

from collections.abc import Generator
from typing import TYPE_CHECKING

from assertpy import assert_that

from lintro.parsers.streaming import collect_streaming_results
from tests.unit.parsers.streaming.conftest import SimpleIssue

if TYPE_CHECKING:
    pass


def test_collects_to_list() -> None:
    """Collect generator results into a list."""

    def gen() -> Generator[SimpleIssue, None, None]:
        yield SimpleIssue(file="a.py")
        yield SimpleIssue(file="b.py")

    results: list[SimpleIssue] = collect_streaming_results(gen())

    assert_that(results).is_length(2)
    assert_that(results[0].file).is_equal_to("a.py")
    assert_that(results[1].file).is_equal_to("b.py")


def test_empty_generator_returns_empty_list() -> None:
    """Empty generator returns empty list."""

    def gen() -> Generator[SimpleIssue, None, None]:
        return
        yield

    results: list[SimpleIssue] = collect_streaming_results(gen())

    assert_that(results).is_empty()
