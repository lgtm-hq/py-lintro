"""Tests for ``ToolManager.get_parallel_batches``.

Since #1742 batching is derived from the same claims DAG that orders a
sequential run, so these tests assert against real registered tools rather
than a mocked ``conflicts_with`` table.
"""

from __future__ import annotations

import pytest
from assertpy import assert_that

import lintro.tools.core.tool_manager as tool_manager_module
from lintro.enums.capability import Cap
from lintro.tools.core.scheduler import DerivedOrder, OrderEdge
from lintro.tools.core.tool_manager import ToolManager


def _edge(*, before: str, after: str) -> OrderEdge:
    """Build a FIX -> CHECK edge between two tools on ``*.py``.

    Args:
        before: Tool that must run first.
        after: Tool that must run second.

    Returns:
        The corresponding derived edge.
    """
    return OrderEdge(
        before=before,
        after=after,
        pattern="*.py",
        before_capability=Cap.FIX,
        after_capability=Cap.CHECK,
    )


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
    # Batch membership, not flattened position: a single [ruff, black, mypy]
    # batch would satisfy an index comparison while running all three
    # concurrently, which is exactly what the derived edges forbid.
    depth = {name: index for index, batch in enumerate(batches) for name in batch}
    assert_that(depth["ruff"]).is_less_than(depth["black"])
    assert_that(depth["black"]).is_less_than(depth["mypy"])


def test_get_parallel_batches_preserves_input_order_within_a_batch() -> None:
    """Independent tools keep the order they were handed in."""
    batches = get_parallel_batches(["yamllint", "hadolint"])

    assert_that(batches[0]).is_equal_to(["yamllint", "hadolint"])


def test_get_parallel_batches_covers_every_selected_tool_once() -> None:
    """Batching partitions the selection; nothing is dropped or duplicated."""
    tools = ["ruff", "black", "mypy", "yamllint", "hadolint"]

    batches = get_parallel_batches(tools)
    flattened = [name for batch in batches for name in batch]
    depth = {name: index for index, batch in enumerate(batches) for name in batch}

    assert_that(sorted(flattened)).is_equal_to(sorted(tools))
    # Unconstrained tools keep running alongside the DAG root rather than
    # being pushed into their own batches once any edge exists.
    assert_that(depth["yamllint"]).is_equal_to(depth["ruff"])
    assert_that(depth["hadolint"]).is_equal_to(depth["ruff"])


def test_get_parallel_batches_normalizes_mixed_case_names() -> None:
    """Derived edges are lowercase, so batching lowercases its input too."""
    batches = get_parallel_batches(["RUFF", "Black"])

    depth = {name: index for index, batch in enumerate(batches) for name in batch}
    assert_that(sorted(depth)).is_equal_to(["black", "ruff"])
    assert_that(depth["ruff"]).is_less_than(depth["black"])


def test_get_parallel_batches_rejects_duplicate_names() -> None:
    """A selection naming the same tool twice is a caller error."""
    assert_that(get_parallel_batches).raises(ValueError).when_called_with(
        ["ruff", "RUFF"],
    )


def test_get_parallel_batches_serialises_a_stalled_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cycle must not collapse into one concurrent batch.

    ``_linearize`` breaks a cycle by emitting the alphabetically first
    remaining tool; batching mirrors that rather than running tools that
    still constrain each other at the same time.

    Args:
        monkeypatch: Pytest fixture used to substitute a cyclic report.
    """
    cyclic = DerivedOrder(
        tools=("a_tool", "b_tool"),
        edges=(
            _edge(before="a_tool", after="b_tool"),
            _edge(before="b_tool", after="a_tool"),
        ),
        cycles=(),
    )
    monkeypatch.setattr(
        tool_manager_module,
        "build_order_report",
        lambda _names: cyclic,
        raising=True,
    )

    batches = ToolManager().get_parallel_batches(["b_tool", "a_tool"])

    assert_that(batches).is_equal_to([["a_tool"], ["b_tool"]])


def test_get_parallel_batches_keeps_a_cycle_successor_last(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaking a cycle must not let a successor run between its members.

    The stall is broken one node at a time, so ``c_tool`` — which follows both
    cycle members — still waits for both instead of being levelled alongside
    them.

    Args:
        monkeypatch: Pytest fixture used to substitute a cyclic report.
    """
    cyclic = DerivedOrder(
        tools=("a_tool", "b_tool", "c_tool"),
        edges=(
            _edge(before="a_tool", after="b_tool"),
            _edge(before="b_tool", after="a_tool"),
            _edge(before="a_tool", after="c_tool"),
            _edge(before="b_tool", after="c_tool"),
        ),
        cycles=(),
    )
    monkeypatch.setattr(
        tool_manager_module,
        "build_order_report",
        lambda _names: cyclic,
        raising=True,
    )

    batches = ToolManager().get_parallel_batches(["c_tool", "b_tool", "a_tool"])

    assert_that(batches).is_equal_to([["a_tool"], ["b_tool"], ["c_tool"]])
