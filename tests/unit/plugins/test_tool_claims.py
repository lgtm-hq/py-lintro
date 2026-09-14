"""Gate over the claim/capability/scope declarations on every tool (#1740).

Each plugin declares *what* it touches and *what it does to it*, and since
#1742 that declaration is the sole input to execution order — the scalar
``priority`` and the unused ``conflicts_with`` list are gone. These tests pin
the declarations against the ``file_patterns``/``can_fix`` fields they now
carry the weight of, and assert the properties the derivation depends on.
"""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.enums.capability import MUTATING_CAPABILITIES, Cap
from lintro.plugins.protocol import ToolDefinition
from lintro.tools.core.scheduler import (
    _pattern_universe,
    _phase_for,
    build_order_report,
)
from lintro.tools.core.tool_manager import ToolManager

#: commitlint reads git commit messages, not files. Its legacy ``["*"]``
#: pattern is a placeholder that keeps shared execution preparation from
#: short-circuiting, so it is the one tool that declares no claims and is the
#: one tool with ``reads_tree=False``.
UNCLAIMED_TOOL: str = "commitlint"

#: Number of builtin tool plugins the registry must expose.
EXPECTED_TOOL_COUNT: int = 45


def _definitions() -> dict[str, ToolDefinition]:
    """Return ``{registry_name: ToolDefinition}`` for every registered plugin.

    Returns:
        Mapping of registry tool name to its tool definition.
    """
    manager = ToolManager()
    return {name: plugin.definition for name, plugin in manager.get_all_tools().items()}


_DEFINITIONS: dict[str, ToolDefinition] = _definitions()
TOOL_NAMES: list[str] = sorted(_DEFINITIONS)


def test_registry_exposes_the_expected_tool_count() -> None:
    """The claim gate covers every builtin tool, not a stale subset."""
    assert_that(TOOL_NAMES).is_length(EXPECTED_TOOL_COUNT)


def test_capability_enum_has_no_lint_or_analyze_member() -> None:
    """``LINT``/``ANALYZE`` are deliberately absent from the taxonomy."""
    members = {member.name for member in Cap}
    assert_that(members).is_equal_to({"FIX", "FORMAT", "CHECK"})


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_tool_declares_at_least_one_claim(name: str) -> None:
    """Every tool declares a claim, except the one that reads no files.

    Args:
        name: Registry tool name under test.
    """
    claims = _DEFINITIONS[name].claims
    if name == UNCLAIMED_TOOL:
        assert_that(claims).is_empty()
        return
    assert_that(claims).is_not_empty()


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_claim_capabilities_are_known_and_non_empty(name: str) -> None:
    """No claim is capability-less, and none names an unknown capability.

    Args:
        name: Registry tool name under test.
    """
    for claim in _DEFINITIONS[name].claims:
        assert_that(claim.capabilities).is_not_empty()
        assert_that(set(claim.capabilities).issubset(set(Cap))).is_true()


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_every_legacy_file_pattern_appears_in_a_claim(name: str) -> None:
    """Claims cover the legacy ``file_patterns`` they will replace.

    Args:
        name: Registry tool name under test.
    """
    definition = _DEFINITIONS[name]
    if name == UNCLAIMED_TOOL:
        return
    if not definition.file_patterns:
        # osv-scanner does its own discovery; a pattern-less claim is correct.
        assert_that([claim.patterns for claim in definition.claims]).is_equal_to([[]])
        return
    claimed: set[str] = set()
    for claim in definition.claims:
        claimed.update(claim.patterns)
    assert_that(claimed).contains(*definition.file_patterns)


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_every_claimed_pattern_appears_in_file_patterns(name: str) -> None:
    """Claims never reach past the legacy ``file_patterns`` they mirror.

    The reverse direction is covered above. Both are needed: a claim that
    silently widens a tool's reach would add derived edges for files the tool
    is never handed.

    Args:
        name: Registry tool name under test.
    """
    definition = _DEFINITIONS[name]
    claimed: set[str] = set()
    for claim in definition.claims:
        claimed.update(claim.patterns)
    assert_that(claimed.issubset(set(definition.file_patterns))).described_as(
        f"{name}: claims {sorted(claimed - set(definition.file_patterns))} "
        f"outside file_patterns",
    ).is_true()


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_mutating_capabilities_agree_with_can_fix(name: str) -> None:
    """A tool holds ``FIX``/``FORMAT`` if and only if it declares ``can_fix``.

    Args:
        name: Registry tool name under test.
    """
    definition = _DEFINITIONS[name]
    if name == UNCLAIMED_TOOL:
        return
    mutates = any(claim.is_mutating for claim in definition.claims)
    assert_that(mutates).is_equal_to(definition.can_fix)


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_non_mutating_tools_declare_check(name: str) -> None:
    """A tool that rewrites nothing must claim ``CHECK`` somewhere.

    Args:
        name: Registry tool name under test.
    """
    definition = _DEFINITIONS[name]
    if name == UNCLAIMED_TOOL or definition.can_fix:
        return
    capabilities: set[Cap] = set()
    for claim in definition.claims:
        capabilities.update(claim.capabilities)
    assert_that(capabilities).is_equal_to({Cap.CHECK})


def test_only_commitlint_opts_out_of_reading_the_tree() -> None:
    """``reads_tree=False`` is reserved for the commit-message linter."""
    unordered = sorted(
        name for name, definition in _DEFINITIONS.items() if not definition.reads_tree
    )
    assert_that(unordered).is_equal_to([UNCLAIMED_TOOL])


def test_format_authority_is_uncontested_in_the_derived_graph() -> None:
    """No pattern group has two tools in the derived ``FORMAT`` phase.

    Dual formatting authority is the defect the epic exists to remove. The
    check runs over the derivation's own pattern universe and its universal
    ``*`` subsumption rather than exact pattern strings, so a tool claiming
    ``*.py`` and one claiming ``*`` are compared rather than filed apart.
    """
    claims_by_tool = {name: _DEFINITIONS[name].claims for name in TOOL_NAMES}

    contested = {
        pattern: holders
        for pattern in _pattern_universe(claims_by_tool)
        if len(
            holders := sorted(
                name
                for name in TOOL_NAMES
                if _phase_for(claims_by_tool[name], pattern) is Cap.FORMAT
            ),
        )
        > 1
    }

    assert_that(contested).is_equal_to({})


def test_ruff_yields_format_to_black_on_python() -> None:
    """Both declare ``FORMAT`` on ``*.py``; the derivation puts ruff in FIX.

    This is the epic's one real authority contest. It is resolved by the
    phase-per-pattern rule rather than by dropping a tool, so ruff keeps its
    fix capability and simply runs first.
    """
    claims_by_tool = {name: _DEFINITIONS[name].claims for name in TOOL_NAMES}

    declared = {
        name: {
            cap
            for claim in claims_by_tool[name]
            if "*.py" in claim.patterns
            for cap in claim.capabilities
        }
        for name in ("ruff", "black")
    }

    assert_that(declared["ruff"]).contains(Cap.FORMAT)
    assert_that(declared["black"]).contains(Cap.FORMAT)
    assert_that(_phase_for(claims_by_tool["ruff"], "*.py")).is_equal_to(Cap.FIX)
    assert_that(_phase_for(claims_by_tool["black"], "*.py")).is_equal_to(Cap.FORMAT)


def test_derived_graph_over_every_tool_is_a_dag() -> None:
    """The full builtin claim set derives a cycle-free order (#1742)."""
    report = build_order_report(TOOL_NAMES)

    assert_that(report.cycles).is_empty()
    assert_that(report.tools).is_length(EXPECTED_TOOL_COUNT)


def test_tool_definition_scheduling_defaults_are_conservative() -> None:
    """An external plugin that declares nothing is treated safely.

    No claims means no derived edges, ``reads_tree`` means it runs after
    mutation settles, and ``partitionable=False`` means its file set is never
    narrowed — the safe direction for all three.
    """
    definition = ToolDefinition(name="example", description="Example tool")

    assert_that(definition.claims).is_equal_to([])
    assert_that(definition.reads_tree).is_true()
    assert_that(definition.partitionable).is_false()


def test_mutating_capabilities_constant_matches_the_enum() -> None:
    """The mutating-capability set is exactly ``FIX`` and ``FORMAT``."""
    assert_that(set(MUTATING_CAPABILITIES)).is_equal_to({Cap.FIX, Cap.FORMAT})


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_scalar_priority_is_gone(name: str) -> None:
    """No definition carries a scalar ``priority`` any more (#1742).

    Args:
        name: Registry tool name under test.
    """
    assert_that(hasattr(_DEFINITIONS[name], "priority")).is_false()


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_conflicts_with_is_gone(name: str) -> None:
    """No definition carries ``conflicts_with`` any more (#1742).

    Args:
        name: Registry tool name under test.
    """
    assert_that(hasattr(_DEFINITIONS[name], "conflicts_with")).is_false()
