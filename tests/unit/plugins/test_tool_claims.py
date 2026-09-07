"""Gate over the claim/capability/scope declarations on every tool (#1740).

Step 2 of epic #1735 makes each plugin declare *what* it touches and *what it
does to it*, so step 3 can derive execution order instead of reading the
scalar ``DEFAULT_TOOL_PRIORITIES`` table. Nothing consumes the declarations
yet; these tests pin them so the data cannot drift away from the legacy
``file_patterns``/``can_fix`` fields it will eventually replace.
"""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.enums.capability import MUTATING_CAPABILITIES, Cap
from lintro.plugins.protocol import ToolDefinition
from lintro.tools.core.tool_manager import ToolManager

#: commitlint reads git commit messages, not files. Its legacy ``["*"]``
#: pattern is a placeholder that keeps shared execution preparation from
#: short-circuiting, so it is the one tool that declares no claims and is the
#: one tool with ``reads_tree=False``.
UNCLAIMED_TOOL: str = "commitlint"

#: Number of builtin tool plugins the registry must expose.
EXPECTED_TOOL_COUNT: int = 43


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


def test_format_authority_is_contested_only_by_ruff_and_black() -> None:
    """``FORMAT`` is held by one tool per pattern, bar the known contest.

    Dual formatting authority is the defect the epic exists to remove, so the
    contested set is pinned exactly: ruff vs black on Python, and nothing
    else. Step 4's resolver then has a fixed starting point rather than a
    rediscovered one.
    """
    owners: dict[str, list[str]] = {}
    for name in TOOL_NAMES:
        for claim in _DEFINITIONS[name].claims:
            if Cap.FORMAT not in claim.capabilities:
                continue
            for pattern in claim.patterns:
                owners.setdefault(pattern, []).append(name)
    contested = {
        pattern: sorted(names) for pattern, names in owners.items() if len(names) > 1
    }

    assert_that(contested).is_equal_to(
        {"*.py": ["black", "ruff"], "*.pyi": ["black", "ruff"]},
    )


def test_mutating_capabilities_constant_matches_the_enum() -> None:
    """The mutating-capability set is exactly ``FIX`` and ``FORMAT``."""
    assert_that(set(MUTATING_CAPABILITIES)).is_equal_to({Cap.FIX, Cap.FORMAT})


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_priority_is_still_declared(name: str) -> None:
    """``priority`` survives step 2 untouched; ordering is unchanged.

    Args:
        name: Registry tool name under test.
    """
    assert_that(_DEFINITIONS[name].priority).is_instance_of(int)
    assert_that(_DEFINITIONS[name].priority).is_greater_than_or_equal_to(0)
