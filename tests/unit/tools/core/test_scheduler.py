"""Tests for the claims-derived order scheduler (#1741, #1742).

The scheduler derives an execution order from declared claims, and since
#1742 that order is the one lintro runs, so these tests also pin that the
live ordering entry point returns exactly what the scheduler derives.
"""

from __future__ import annotations

from assertpy import assert_that

from lintro.enums.capability import Cap
from lintro.models.core.claim import Claim
from lintro.tools.core.scheduler import (
    PHASE_ORDER,
    build_order_report,
    collect_tool_claims,
    derive_execution_order,
    derive_order,
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


def test_collect_tool_claims_reads_the_registry() -> None:
    """Registered tools hand back the claims their definitions declare."""
    claims = collect_tool_claims(["ruff", "black"])

    assert_that(sorted(claims)).is_equal_to(["black", "ruff"])
    ruff_caps: set[Cap] = set()
    for claim in claims["ruff"]:
        ruff_caps |= claim.capabilities
    assert_that(ruff_caps).contains(Cap.FIX)


def test_build_order_report_recovers_ruff_before_black() -> None:
    """The headline pairing from #1735 falls out of the derivation."""
    report = build_order_report(["black", "ruff"])

    assert_that(list(report.tools)).is_equal_to(["ruff", "black"])
    assert_that(report.cycles).is_empty()
    assert_that(
        [(edge.before, edge.after) for edge in report.edges],
    ).contains(("ruff", "black"))


def test_derive_execution_order_normalizes_tool_names() -> None:
    """Names are lowercased before they reach the registry."""
    assert_that(derive_execution_order(["RUFF", "Black"])).is_equal_to(
        ["ruff", "black"],
    )


def test_derive_execution_order_tolerates_an_unresolvable_tool() -> None:
    """An unresolvable name stays in the order, unconstrained."""
    order = derive_execution_order(["ruff", "not-a-registered-tool"])

    assert_that(sorted(order)).is_equal_to(["not-a-registered-tool", "ruff"])


def test_live_order_is_exactly_the_derived_order() -> None:
    """``get_tool_execution_order`` returns what the scheduler derives."""
    from lintro.tools import tool_manager

    selection = ["black", "ruff", "mypy"]

    assert_that(
        tool_manager.get_tool_execution_order(selection),
    ).is_equal_to(derive_execution_order(selection))


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
