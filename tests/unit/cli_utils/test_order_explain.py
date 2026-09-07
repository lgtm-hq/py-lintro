"""Tests for the derived-order rendering (#1741, #1742)."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.cli_utils.order_explain import (
    DERIVED_NOTE,
    EXPLAIN_HEADER,
    MAX_DOCTOR_CONSTRAINTS,
    MAX_EDGES_PER_TOOL,
    emit_order_explanation,
    format_doctor_order_section,
    format_order_report,
)
from lintro.enums.capability import Cap
from lintro.tools.core.scheduler import DerivedOrder, OrderCycle, OrderEdge

GOLDEN_REPORT: list[str] = [
    "Execution order (derived from tool claims)",
    "  This is the order that runs. It is derived from what each tool claims "
    "to touch, not configured.",
    "",
    "    1. ruff",
    "        (unconstrained; alphabetical tiebreak)",
    "    2. black",
    "        after ruff — *.py: ruff(fix) -> black(format)",
    "        after ruff — *.pyi: ruff(fix) -> black(format)",
    "",
    "  Cycles (0): the derived graph is a DAG.",
]


def _edge(pattern: str, *, after: str = "black") -> OrderEdge:
    """Build a ruff -> ``after`` edge for one pattern.

    Args:
        pattern: Pattern the edge came from.
        after: Tool the edge constrains.

    Returns:
        The edge.
    """
    return OrderEdge(
        before="ruff",
        after=after,
        pattern=pattern,
        before_capability=Cap.FIX,
        after_capability=Cap.FORMAT,
    )


def _report(
    *,
    tools: tuple[str, ...] = ("ruff", "black"),
    edges: tuple[OrderEdge, ...] = (),
    cycles: tuple[OrderCycle, ...] = (),
) -> DerivedOrder:
    """Build a derived order for rendering.

    Args:
        tools: Tools in derived order.
        edges: Derived edges.
        cycles: Cycles to include.

    Returns:
        The derived order.
    """
    return DerivedOrder(tools=tools, edges=edges, cycles=cycles)


def test_format_order_report_matches_golden() -> None:
    """The rendered explanation is byte-stable."""
    report = _report(edges=(_edge("*.py"), _edge("*.pyi")))

    assert_that(format_order_report(report)).is_equal_to(GOLDEN_REPORT)


def test_format_order_report_handles_empty_selection() -> None:
    """An empty tool set says so rather than rendering a blank listing."""
    assert_that(format_order_report(_report(tools=()))).contains(
        "    (no tools selected)",
    )


def test_format_order_report_truncates_long_constraint_lists() -> None:
    """A `*` claim cannot flood the output with one pattern per line."""
    edges = tuple(_edge(f"*.x{index}") for index in range(MAX_EDGES_PER_TOOL + 3))

    lines = format_order_report(_report(edges=edges))

    assert_that(lines).contains("        ... and 3 more constraint(s)")


def test_format_order_report_renders_cycles() -> None:
    """A cycle is named with its tools and the patterns that closed it."""
    cycle = OrderCycle(tools=("one", "two"), edges=(_edge("*.a"), _edge("*.b")))

    lines = format_order_report(_report(cycles=(cycle,)))

    assert_that(lines).contains("  Cycles (1):")
    assert_that(lines).contains("    one <-> two")
    assert_that(lines).contains("      patterns: *.a, *.b")
    assert_that(lines).contains(
        "  Cycles are broken alphabetically so a run stays deterministic.",
    )


def test_doctor_section_is_compact_and_capped() -> None:
    """The doctor section summarises rather than reprinting the whole order."""
    followers = tuple(f"tool{index}" for index in range(MAX_DOCTOR_CONSTRAINTS + 2))
    report = _report(
        tools=("ruff", *followers),
        edges=tuple(_edge("*.py", after=name) for name in followers),
    )

    lines = format_doctor_order_section(report)

    assert_that(lines[0]).is_equal_to("  Execution order (derived)")
    assert_that(lines[1]).is_equal_to(f"    {DERIVED_NOTE}")
    assert_that(lines[2]).is_equal_to("    tools: 8  constraints: 7  cycles: 0")
    assert_that(lines).contains("    ... and 2 more constrained tool(s)")
    assert_that(lines[-1]).is_equal_to(
        "    Run 'lintro check --explain-order' for the full order.",
    )


def test_emit_order_explanation_prints_and_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--explain-order`` prints the order and exits without running tools."""
    with pytest.raises(SystemExit) as excinfo:
        emit_order_explanation(
            tools="ruff,black",
            action="check",
            paths=["."],
        )

    assert_that(excinfo.value.code).is_equal_to(0)
    out = capsys.readouterr().out
    assert_that(out).contains(EXPLAIN_HEADER)
    assert_that(out).contains("after ruff — *.py: ruff(fix) -> black(format)")


def test_emit_order_explanation_reports_an_unknown_tool(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An invalid selection exits 1 with the resolver's message on stderr."""
    with pytest.raises(SystemExit) as excinfo:
        emit_order_explanation(
            tools="definitely-not-a-tool",
            action="check",
            paths=["."],
        )

    assert_that(excinfo.value.code).is_equal_to(1)
    assert_that(capsys.readouterr().err).is_not_empty()
