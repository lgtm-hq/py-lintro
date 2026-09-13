"""Tests for the derived-order rendering (#1741, #1742)."""

from __future__ import annotations

from typing import NoReturn

import pytest
from assertpy import assert_that

import lintro.cli_utils.order_explain as order_explain
from lintro.cli_utils.order_explain import (
    DERIVED_NOTE,
    EXPLAIN_HEADER,
    MAX_DOCTOR_CONSTRAINTS,
    MAX_DOCTOR_DEMOTIONS,
    MAX_EDGES_PER_TOOL,
    emit_order_explanation,
    format_doctor_order_section,
    format_order_report,
    format_ownership_notice,
)
from lintro.enums.capability import Cap
from lintro.tools.core.scheduler import (
    DerivedOrder,
    FormatDemotion,
    OrderCycle,
    OrderEdge,
)

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
    "",
    "  Format ownership (0): no two tools contend for one scope.",
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
    demotions: tuple[FormatDemotion, ...] = (),
) -> DerivedOrder:
    """Build a derived order for rendering.

    Args:
        tools: Tools in derived order.
        edges: Derived edges.
        cycles: Cycles to include.
        demotions: Format-owner demotions to include.

    Returns:
        The derived order.
    """
    return DerivedOrder(
        tools=tools,
        edges=edges,
        cycles=cycles,
        demotions=demotions,
    )


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
    assert_that(lines[2]).is_equal_to(
        "    tools: 8  constraints: 7  cycles: 0  format demotions: 0",
    )
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


def test_format_order_report_names_the_format_owner() -> None:
    """A demotion is reported with the winner, loser, scope and override key."""
    demotion = FormatDemotion(
        winner="black",
        loser="ruff",
        scope="*.py",
        rule="fewer mutating capabilities",
    )

    lines = format_order_report(_report(demotions=(demotion,)))

    assert_that(lines).contains("  Format ownership (1):")
    assert_that(lines).contains(
        "    *.py: black owns FORMAT; ruff(format) demoted "
        "(fewer mutating capabilities; override with execution.precedence)",
    )


def test_doctor_section_names_the_format_owner() -> None:
    """The compact doctor section carries the demotion too."""
    demotion = FormatDemotion(
        winner="black",
        loser="ruff",
        scope="*.py",
        rule="fewer mutating capabilities",
    )

    lines = format_doctor_order_section(_report(demotions=(demotion,)))

    assert_that(lines).contains(
        "    *.py: black owns FORMAT, ruff demoted (fewer mutating capabilities)",
    )


def test_format_ownership_notice_reports_a_contending_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``lintro init`` says who will own FORMAT and which key changes it.

    Args:
        monkeypatch: pytest monkeypatch fixture.
    """
    demotion = FormatDemotion(
        winner="black",
        loser="ruff",
        scope="*.py",
        rule="fewer mutating capabilities",
    )
    monkeypatch.setattr(
        order_explain,
        "build_order_report",
        lambda _names, **_kwargs: _report(demotions=(demotion,)),
    )

    lines = format_ownership_notice(["ruff", "black"])

    assert_that(lines).contains("  Format ownership:")
    assert_that(lines).contains(
        "    *.py: black owns FORMAT, ruff demoted (fewer mutating capabilities)",
    )
    assert_that(lines[-1]).contains("execution.precedence")


def test_format_ownership_notice_is_silent_when_nothing_contends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No contention means no advisory line at all.

    Args:
        monkeypatch: pytest monkeypatch fixture.
    """
    monkeypatch.setattr(
        order_explain,
        "build_order_report",
        lambda _names, **_kwargs: _report(),
    )

    assert_that(format_ownership_notice(["ruff"])).is_empty()


def test_format_ownership_notice_never_fails_init(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unusable config must not take ``lintro init`` down with it.

    The notice is advisory, so a scheduler that cannot answer degrades to
    silence rather than to a traceback on the command that writes the config.

    Args:
        monkeypatch: pytest monkeypatch fixture.
    """

    def _raise(_names: object, **_kwargs: object) -> NoReturn:
        """Stand in for a scheduler that cannot resolve the selection.

        Args:
            _names: Ignored tool names.
            **_kwargs: Ignored keyword arguments.

        Raises:
            ValueError: Always.
        """
        raise ValueError("unresolvable")

    monkeypatch.setattr(order_explain, "build_order_report", _raise)

    assert_that(format_ownership_notice(["ruff", "black"])).is_empty()


def test_doctor_section_truncates_a_long_demotion_list() -> None:
    """A wide contending set cannot flood the compact doctor section."""
    demotions = tuple(
        FormatDemotion(
            winner="black",
            loser=f"tool{index}",
            scope="*.py",
            rule="alphabetical tool id",
        )
        for index in range(MAX_DOCTOR_DEMOTIONS + 2)
    )

    lines = format_doctor_order_section(_report(demotions=demotions))

    assert_that(lines).contains("    ... and 2 more demotion(s)")


@pytest.mark.parametrize(
    ("action", "dry_run", "expected"),
    [("fmt", False, True), ("fmt", True, False), ("check", False, False)],
)
def test_explain_forwards_the_scope_and_the_mutating_flag(
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    dry_run: bool,
    expected: bool,
) -> None:
    """The explanation is built for the invocation it claims to describe.

    A mutating run derives write-conflict edges and a read-only one does not,
    so ``--explain-order`` has to pass the run's paths and the right flag or
    it prints an order that invocation would never have used. ``fmt
    --dry-run`` rewrites nothing, so it belongs on the read-only side.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        action: Action the invocation would perform.
        dry_run: Whether the invocation previews rather than writes.
        expected: Whether write conflicts should be derived.
    """
    captured: dict[str, object] = {}

    def _capture(names: object, **kwargs: object) -> DerivedOrder:
        """Record the scheduler call and return an empty report.

        Args:
            names: Tool names the explanation resolved.
            **kwargs: Scheduler keyword arguments to record.

        Returns:
            DerivedOrder: An empty report.
        """
        captured["names"] = names
        captured.update(kwargs)
        return _report(tools=())

    monkeypatch.setattr(order_explain, "build_order_report", _capture)

    order_explain.explain_order_lines(
        "ruff",
        action,
        ["src"],
        dry_run=dry_run,
    )

    assert_that(captured["paths"]).is_equal_to(["src"])
    assert_that(captured["write_conflicts"]).is_equal_to(expected)


def test_explain_forwards_the_whole_run_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Excludes and the diff base reach the scheduler, not just the paths.

    Overlap is resolved from the files each tool would actually be handed, so
    an explanation that dropped the excludes or the diff base would report
    conflicts and demotions for files the run could never touch.

    Args:
        monkeypatch: pytest monkeypatch fixture.
    """
    captured: dict[str, object] = {}

    def _capture(names: object, **kwargs: object) -> DerivedOrder:
        """Record the scheduler call and return an empty report.

        Args:
            names: Tool names the explanation resolved.
            **kwargs: Scheduler keyword arguments to record.

        Returns:
            DerivedOrder: An empty report.
        """
        captured["names"] = names
        captured.update(kwargs)
        return _report(tools=())

    monkeypatch.setattr(order_explain, "build_order_report", _capture)
    monkeypatch.setattr(
        order_explain,
        "_explained_diff_base",
        lambda _base, _paths: "origin/main",
    )

    order_explain.explain_order_lines(
        "ruff",
        "fmt",
        ["src"],
        exclude="build,dist",
        include_venv=True,
        diff_base="HEAD~1",
    )

    assert_that(captured["exclude"]).is_equal_to("build,dist")
    assert_that(captured["include_venv"]).is_true()
    assert_that(captured["diff_base"]).is_equal_to("origin/main")


def test_an_unresolvable_diff_base_explains_the_unnarrowed_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A preview must not be the thing that fails on a bad ref.

    Args:
        monkeypatch: pytest monkeypatch fixture.
    """

    def _raise(**_kwargs: object) -> NoReturn:
        """Stand in for a diff base that cannot be resolved.

        Args:
            **_kwargs: Ignored preflight arguments.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError("no such ref")

    monkeypatch.setattr(
        "lintro.utils.execution.run_preflight.resolve_diff_scope",
        _raise,
    )

    assert_that(
        order_explain._explained_diff_base("nope", ["src"]),
    ).is_none()
