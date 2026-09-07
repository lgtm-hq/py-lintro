"""Tests for the shadow-mode order scheduler (#1741).

The scheduler derives an execution order from declared claims and diffs it
against the order lintro runs today. Nothing it produces is allowed to reach
execution, so these tests also pin that the live ordering entry point is
untouched.
"""

from __future__ import annotations

from assertpy import assert_that

from lintro.enums.capability import Cap
from lintro.models.core.claim import Claim
from lintro.tools.core.scheduler import (
    PHASE_ORDER,
    OrderShadowReport,
    build_shadow_report,
    collect_tool_claims,
    derive_order,
    diff_orders,
)


def _claims(*capability_sets: tuple[list[str], set[Cap]]) -> list[Claim]:
    """Build a claim list from ``(patterns, capabilities)`` pairs.

    Args:
        *capability_sets: Pairs of patterns and capabilities.

    Returns:
        The corresponding claims.
    """
    return [
        Claim(patterns=patterns, capabilities=capabilities)
        for patterns, capabilities in capability_sets
    ]


def test_phase_order_is_fix_format_check() -> None:
    """The derivation phases are FIX, then FORMAT, then CHECK."""
    assert_that(list(PHASE_ORDER)).is_equal_to([Cap.FIX, Cap.FORMAT, Cap.CHECK])


def test_linear_chain_orders_fix_before_format_before_check() -> None:
    """A single pattern with all three phases derives one linear order."""
    derived = derive_order(
        {
            "checker": _claims((["*.py"], {Cap.CHECK})),
            "fixer": _claims((["*.py"], {Cap.FIX})),
            "formatter": _claims((["*.py"], {Cap.FORMAT})),
        },
    )

    assert_that(list(derived.tools)).is_equal_to(["fixer", "formatter", "checker"])
    assert_that(derived.cycles).is_empty()


def test_multi_capability_tool_occupies_its_earliest_phase() -> None:
    """ruff-shaped ``{FIX, FORMAT, CHECK}`` sits in FIX, so it precedes black.

    A tool is invoked once, so the CHECK it also holds must not pull it after
    the dedicated formatter — otherwise the pair would derive a cycle.
    """
    derived = derive_order(
        {
            "black": _claims((["*.py"], {Cap.FORMAT, Cap.CHECK})),
            "ruff": _claims((["*.py"], {Cap.FIX, Cap.FORMAT, Cap.CHECK})),
        },
    )

    assert_that(list(derived.tools)).is_equal_to(["ruff", "black"])
    assert_that(derived.cycles).is_empty()


def test_diamond_shape_breaks_ties_alphabetically() -> None:
    """Independent tools at the same phase order alphabetically."""
    derived = derive_order(
        {
            "fixer": _claims((["*.css", "*.js"], {Cap.FIX})),
            "zebra_check": _claims((["*.css"], {Cap.CHECK})),
            "alpha_check": _claims((["*.js"], {Cap.CHECK})),
        },
    )

    assert_that(list(derived.tools)).is_equal_to(
        ["fixer", "alpha_check", "zebra_check"],
    )
    assert_that(derived.cycles).is_empty()


def test_cycle_is_detected_and_names_tools_and_patterns() -> None:
    """A two-pattern disagreement is reported as a cycle, not silently sorted."""
    derived = derive_order(
        {
            "one": _claims((["*.a"], {Cap.FIX}), (["*.b"], {Cap.CHECK})),
            "two": _claims((["*.b"], {Cap.FIX}), (["*.a"], {Cap.CHECK})),
        },
    )

    assert_that(derived.cycles).is_length(1)
    cycle = derived.cycles[0]
    assert_that(list(cycle.tools)).is_equal_to(["one", "two"])
    assert_that(list(cycle.patterns)).is_equal_to(["*.a", "*.b"])
    # Linearisation still returns a total, deterministic order.
    assert_that(sorted(derived.tools)).is_equal_to(["one", "two"])
    assert_that(list(derived.tools)).is_equal_to(["one", "two"])


def test_pattern_less_claim_produces_no_edges() -> None:
    """A project-scoped claim (osv-scanner shaped) is unordered."""
    derived = derive_order(
        {
            "project_scanner": _claims(([], {Cap.CHECK})),
            "fixer": _claims((["*.py"], {Cap.FIX})),
        },
    )

    assert_that(derived.edges).is_empty()
    assert_that(list(derived.tools)).is_equal_to(["fixer", "project_scanner"])


def test_universal_pattern_joins_every_pattern_group() -> None:
    """A ``*`` FIX claim precedes narrower CHECK claims."""
    derived = derive_order(
        {
            "typos": _claims((["*"], {Cap.FIX, Cap.CHECK})),
            "mypy": _claims((["*.py"], {Cap.CHECK})),
        },
    )

    assert_that(list(derived.tools)).is_equal_to(["typos", "mypy"])
    assert_that([edge.pattern for edge in derived.edges]).contains("*.py")


def test_tool_without_claims_stays_unordered() -> None:
    """Commitlint declares no claims, so it takes its alphabetical slot."""
    derived = derive_order(
        {
            "commitlint": [],
            "ruff": _claims((["*.py"], {Cap.FIX})),
        },
    )

    assert_that(derived.edges).is_empty()
    assert_that(list(derived.tools)).is_equal_to(["commitlint", "ruff"])


def test_diff_reports_only_violated_constraints() -> None:
    """A current order that satisfies every edge yields no differences."""
    derived = derive_order(
        {
            "black": _claims((["*.py"], {Cap.FORMAT})),
            "ruff": _claims((["*.py"], {Cap.FIX})),
        },
    )

    assert_that(diff_orders(derived, ["ruff", "black"])).is_empty()

    differences = diff_orders(derived, ["black", "ruff"])
    assert_that(differences).is_length(1)
    assert_that(differences[0].before).is_equal_to("ruff")
    assert_that(differences[0].after).is_equal_to("black")
    assert_that([edge.reason for edge in differences[0].edges]).is_equal_to(
        ["*.py: ruff(fix) -> black(format)"],
    )


def test_diff_ignores_tools_outside_the_current_order() -> None:
    """Edges touching a tool the run did not select are not reported."""
    derived = derive_order(
        {
            "black": _claims((["*.py"], {Cap.FORMAT})),
            "ruff": _claims((["*.py"], {Cap.FIX})),
        },
    )

    assert_that(diff_orders(derived, ["black"])).is_empty()


def test_collect_tool_claims_reads_the_registry() -> None:
    """Registered tools hand back the claims their definitions declare."""
    claims = collect_tool_claims(["ruff", "black"])

    assert_that(sorted(claims)).is_equal_to(["black", "ruff"])
    ruff_caps: set[Cap] = set()
    for claim in claims["ruff"]:
        ruff_caps |= claim.capabilities
    assert_that(ruff_caps).contains(Cap.FIX)


def test_build_shadow_report_recovers_ruff_before_black() -> None:
    """The headline disagreement from #1735 shows up in the report."""
    report = build_shadow_report(["black", "ruff"])

    assert_that(report).is_instance_of(OrderShadowReport)
    assert_that(list(report.current)).is_equal_to(["black", "ruff"])
    assert_that(list(report.derived)).is_equal_to(["ruff", "black"])
    assert_that(report.agrees).is_false()
    assert_that(
        [(d.before, d.after) for d in report.differences],
    ).is_equal_to([("ruff", "black")])


def test_build_shadow_report_normalizes_tool_names() -> None:
    """Names are lowercased before they reach the registry."""
    report = build_shadow_report(["RUFF", "Black"])

    assert_that(list(report.current)).is_equal_to(["ruff", "black"])


def test_shadow_mode_does_not_touch_the_live_order() -> None:
    """The live scheduler still returns the scalar-priority order."""
    from lintro.tools import tool_manager

    live = tool_manager.get_tool_execution_order(["ruff", "black"])

    assert_that(list(live)).is_equal_to(["black", "ruff"])


def test_narrow_globs_do_not_subsume_each_other() -> None:
    """Only ``*`` subsumes: ``*.py`` never joins a ``test_*.py`` group."""
    derived = derive_order(
        {
            "broad_fixer": _claims((["*.py"], {Cap.FIX})),
            "narrow_checker": _claims((["test_*.py"], {Cap.CHECK})),
        },
    )

    assert_that(derived.edges).is_empty()
    assert_that(list(derived.tools)).is_equal_to(["broad_fixer", "narrow_checker"])
