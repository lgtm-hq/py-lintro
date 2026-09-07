"""Tests for the shadow-mode order-diff rendering (#1741)."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.cli_utils.order_explain import (
    EXPLAIN_HEADER,
    MAX_DOCTOR_DIFFERENCES,
    MAX_EDGES_PER_DIFFERENCE,
    SHADOW_NOTE,
    emit_order_explanation,
    format_doctor_order_section,
    format_shadow_report,
)
from lintro.enums.capability import Cap
from lintro.tools.core.scheduler import (
    OrderCycle,
    OrderDifference,
    OrderEdge,
    OrderShadowReport,
)

GOLDEN_REPORT: list[str] = [
    "Execution order (shadow mode)",
    "  Reporting only: current is the scalar-priority schedule before post-check splitting; nothing here changes execution.",
    "",
    "  Current (scalar priority):",
    "    1. black",
    "    2. ruff",
    "",
    "  Derived (claims):",
    "    1. ruff",
    "    2. black",
    "",
    "  Differences (1):",
    "    ruff should run before black (current order runs black first)",
    "      *.py: ruff(fix) -> black(format)",
    "      *.pyi: ruff(fix) -> black(format)",
    "",
    "  Cycles (0): the derived graph is a DAG.",
]


def _edge(pattern: str) -> OrderEdge:
    """Build the ruff -> black edge for one pattern.

    Args:
        pattern: Pattern the edge came from.

    Returns:
        The edge.
    """
    return OrderEdge(
        before="ruff",
        after="black",
        pattern=pattern,
        before_capability=Cap.FIX,
        after_capability=Cap.FORMAT,
    )


def _report(
    *,
    differences: tuple[OrderDifference, ...] = (),
    cycles: tuple[OrderCycle, ...] = (),
) -> OrderShadowReport:
    """Build a two-tool shadow report.

    Args:
        differences: Differences to include.
        cycles: Cycles to include.

    Returns:
        The report.
    """
    return OrderShadowReport(
        current=("black", "ruff"),
        derived=("ruff", "black"),
        differences=differences,
        cycles=cycles,
    )


def test_format_shadow_report_matches_golden() -> None:
    """The rendered explanation is byte-stable."""
    report = _report(
        differences=(
            OrderDifference(
                before="ruff",
                after="black",
                edges=(_edge("*.py"), _edge("*.pyi")),
            ),
        ),
    )

    assert_that(format_shadow_report(report)).is_equal_to(GOLDEN_REPORT)


def test_format_shadow_report_states_agreement() -> None:
    """No differences renders an explicit zero, not an empty section."""
    lines = format_shadow_report(_report())

    assert_that(lines[0]).is_equal_to(EXPLAIN_HEADER)
    assert_that(lines).contains("  Differences (0): the current order satisfies every")
    assert_that(lines).contains("  Cycles (0): the derived graph is a DAG.")


def test_format_shadow_report_handles_empty_selection() -> None:
    """An empty tool set says so rather than rendering blank listings."""
    empty = OrderShadowReport(current=(), derived=(), differences=(), cycles=())

    assert_that(format_shadow_report(empty)).contains("    (no tools selected)")


def test_format_shadow_report_truncates_long_pattern_lists() -> None:
    """A `*` claim cannot flood the output with one pattern per line."""
    edges = tuple(_edge(f"*.x{index}") for index in range(MAX_EDGES_PER_DIFFERENCE + 3))
    report = _report(
        differences=(OrderDifference(before="ruff", after="black", edges=edges),),
    )

    lines = format_shadow_report(report)

    assert_that(lines).contains("      ... and 3 more pattern(s)")


def test_format_shadow_report_renders_cycles() -> None:
    """A cycle is named with its tools and the patterns that closed it."""
    cycle = OrderCycle(tools=("one", "two"), edges=(_edge("*.a"), _edge("*.b")))
    report = _report(cycles=(cycle,))

    lines = format_shadow_report(report)

    assert_that(lines).contains("  Cycles (1):")
    assert_that(lines).contains("    one <-> two")
    assert_that(lines).contains("      patterns: *.a, *.b")


def test_doctor_section_is_compact_and_capped() -> None:
    """The doctor section summarises rather than reprinting the whole diff."""
    differences = tuple(
        OrderDifference(before="ruff", after=f"tool{index}", edges=(_edge("*.py"),))
        for index in range(MAX_DOCTOR_DIFFERENCES + 2)
    )
    lines = format_doctor_order_section(_report(differences=differences))

    assert_that(lines[0]).is_equal_to("  Execution order (shadow)")
    assert_that(lines[1]).is_equal_to(f"    {SHADOW_NOTE}")
    assert_that(lines[2]).is_equal_to("    tools: 2  differences: 7  cycles: 0")
    assert_that(lines).contains("    ... and 2 more difference(s)")
    assert_that(lines[-1]).is_equal_to(
        "    Run 'lintro check --explain-order' for the full diff.",
    )


def test_emit_order_explanation_prints_and_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--explain-order`` prints the diff and exits without running tools."""
    with pytest.raises(SystemExit) as excinfo:
        emit_order_explanation(
            tools="ruff,black",
            action="check",
            paths=["."],
        )

    assert_that(excinfo.value.code).is_equal_to(0)
    out = capsys.readouterr().out
    assert_that(out).contains(EXPLAIN_HEADER)
    assert_that(out).contains("ruff should run before black")


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
