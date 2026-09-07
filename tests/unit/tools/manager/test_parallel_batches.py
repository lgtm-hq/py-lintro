"""Tests for ``ToolManager.get_parallel_batches``.

Since #1742 batching is derived from the same claims DAG that orders a
sequential run, so these tests assert against real registered tools rather
than a mocked ``conflicts_with`` table.
"""

from __future__ import annotations

from assertpy import assert_that

from lintro.tools.core.tool_manager import ToolManager


def get_parallel_batches(tools: list[str]) -> list[list[str]]:
    """Batch a selection through a fresh tool manager.

    Args:
        tools: Tool names to batch.

    Returns:
        The derived batches.
    """
    return ToolManager().get_parallel_batches(tools)


def test_get_parallel_batches_empty_tools_list() -> None:
    """An empty selection produces no batches."""
    assert_that(get_parallel_batches([])).is_empty()


def test_get_parallel_batches_single_tool() -> None:
    """A single tool is a single batch."""
    batches = get_parallel_batches(["ruff"])

    assert_that(batches).is_length(1)
    assert_that(batches[0]).is_equal_to(["ruff"])


def test_get_parallel_batches_unrelated_tools_share_one_batch() -> None:
    """Tools with no derived relation run together."""
    batches = get_parallel_batches(["hadolint", "yamllint"])

    assert_that(batches).is_length(1)
    assert_that(batches[0]).contains("hadolint", "yamllint")


def test_get_parallel_batches_separates_ruff_from_black() -> None:
    """A derived edge forces the two tools into consecutive batches."""
    batches = get_parallel_batches(["ruff", "black"])

    assert_that(batches).is_length(2)
    assert_that(batches[0]).is_equal_to(["ruff"])
    assert_that(batches[1]).is_equal_to(["black"])


def test_get_parallel_batches_places_a_checker_after_both_mutators() -> None:
    """A CHECK-phase tool lands in a batch after every mutator it follows."""
    batches = get_parallel_batches(["ruff", "black", "mypy"])

    flattened = [name for batch in batches for name in batch]
    assert_that(flattened).is_length(3)
    assert_that(flattened.index("ruff")).is_less_than(flattened.index("black"))
    assert_that(flattened.index("black")).is_less_than(flattened.index("mypy"))


def test_get_parallel_batches_preserves_input_order_within_a_batch() -> None:
    """Independent tools keep the order they were handed in."""
    batches = get_parallel_batches(["yamllint", "hadolint"])

    assert_that(batches[0]).is_equal_to(["yamllint", "hadolint"])


def test_get_parallel_batches_covers_every_selected_tool_once() -> None:
    """Batching partitions the selection; nothing is dropped or duplicated."""
    tools = ["ruff", "black", "mypy", "yamllint", "hadolint"]

    flattened = [name for batch in get_parallel_batches(tools) for name in batch]

    assert_that(sorted(flattened)).is_equal_to(sorted(tools))
