"""Cross-surface parity for the single effective-config resolver (#2299).

Every AI surface — ``check``/``fmt`` lint enhancement, ``lintro review``,
``lintro doctor``, MCP's ``lintro_review``, and the advisory tools — resolves
effective AI settings through
:func:`lintro.ai.effective_config.resolve_effective_ai_config`. These tests
pin that they agree value for value and source for source, that no surface
reaches past the resolver into the raw ``ai:`` mapping, and that the two
cost-cap rules ADR-0008 keeps separate stay separate: CLI flags and
``LINTRO_AI_*`` overlays may raise or lift ``ai.max_cost_usd``, while MCP's
per-call ``max_cost_usd`` argument may only lower the resolved ceiling.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.ai.effective_config import (
    NO_CLI_OVERRIDES,
    AICliOverrides,
    resolve_effective_ai_config,
)
from lintro.ai.enums import AITransport, ConfigSource
from lintro.ai.interface import resolve_ai_config
from lintro.ai.interface import (
    resolve_effective_ai_config as interface_resolve,
)
from lintro.ai.resolved_ai_config import ResolvedAIConfig
from lintro.config.lintro_config import LintroConfig
from lintro.mcp.toolkits.review import resolve_budget_policy

PROJECT_ROOT = Path(__file__).resolve().parents[3]
LINTRO_PACKAGE = PROJECT_ROOT / "lintro"

#: One project ``ai:`` section every surface in these tests resolves.
RAW_AI_SECTION: dict[str, object] = {
    "enabled": True,
    "lint": True,
    "review": True,
    "provider": "anthropic",
    "model": "claude-sonnet-4-20250514",
    "transport": "api",
    "max_cost_usd": 1.0,
    "max_tokens": 4096,
}


@pytest.fixture(autouse=True)
def _no_env_overlays(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the six ``LINTRO_AI_*`` overlays so the project layer is alone.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    for name in (
        "LINTRO_AI_PROVIDER",
        "LINTRO_AI_MODEL",
        "LINTRO_AI_TRANSPORT",
        "LINTRO_AI_ENABLED",
        "LINTRO_AI_REVIEW",
        "LINTRO_AI_MAX_COST_USD",
    ):
        monkeypatch.delenv(name, raising=False)


def _mcp_resolved(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ResolvedAIConfig:
    """Resolve through the MCP adapter's own entry point.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Stand-in workspace root; used only for error details.

    Returns:
        The resolved config MCP would run with.
    """
    import lintro.ai.availability as availability
    import lintro.config.config_loader as config_loader
    from lintro.mcp.toolkits.review import _resolve_ai_config

    monkeypatch.setattr(availability, "is_ai_available", lambda: True)
    monkeypatch.setattr(
        config_loader,
        "get_config",
        lambda: LintroConfig(ai=dict(RAW_AI_SECTION)),
    )
    _lintro_config, resolved = _resolve_ai_config(workspace=tmp_path)
    return resolved


def _production_call_names(path: Path) -> list[str]:
    """Return every called name in a module, qualified calls included.

    Args:
        path: Python source file to scan.

    Returns:
        The called simple names and attribute names, in source order.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            names.append(func.id)
        elif isinstance(func, ast.Attribute):
            names.append(func.attr)
    return names


# ---------------------------------------------------------------------------
# One resolver
# ---------------------------------------------------------------------------


def test_resolve_from_mapping_has_exactly_one_production_call_site() -> None:
    """The low-level parse is called once in ``lintro``, by the resolver."""
    call_sites = [
        module
        for module in sorted(LINTRO_PACKAGE.rglob("*.py"))
        for name in _production_call_names(module)
        if name == "resolve_from_mapping"
    ]

    assert_that(call_sites).is_length(1)
    assert_that(call_sites[0].name).is_equal_to("effective_config.py")


def test_no_surface_applies_a_post_resolution_transport_override() -> None:
    """``apply_transport_override`` is gone; transport is an overlay (#2299)."""
    hits = [
        module
        for module in sorted(LINTRO_PACKAGE.rglob("*.py"))
        if "apply_transport_override" in module.read_text(encoding="utf-8")
    ]

    assert_that(hits).is_empty()


def test_every_surface_shares_the_same_resolver_object() -> None:
    """The CLI review module imports the resolver rather than copying it."""
    import lintro.cli_utils.commands.review as review_module
    import lintro.mcp.toolkits.review as mcp_module

    assert_that(vars(review_module)["resolve_effective_ai_config"]).is_same_as(
        resolve_effective_ai_config,
    )
    assert_that(mcp_module.__dict__).does_not_contain_key("resolve_from_mapping")


# ---------------------------------------------------------------------------
# Cross-surface parity
# ---------------------------------------------------------------------------


def test_all_surfaces_resolve_identical_values_and_sources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """check, fix, review CLI, MCP and doctor agree on config and provenance.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Stand-in MCP workspace root.
    """
    lintro_config = LintroConfig(ai=dict(RAW_AI_SECTION))

    # check / fmt lint enhancement, with no --transport on this invocation.
    lint_path = interface_resolve(lintro_config, cli_overrides=NO_CLI_OVERRIDES)
    # ``lintro review`` with no flags set.
    review_cli = resolve_effective_ai_config(
        lintro_config.ai,
        cli_overrides=AICliOverrides(),
    )
    # MCP's ``lintro_review``.
    mcp = _mcp_resolved(monkeypatch, tmp_path)
    # ``lintro doctor`` and the advisory tools, which need values only.
    doctor = resolve_ai_config(lintro_config)

    for resolved in (lint_path, review_cli, mcp):
        assert_that(resolved.config).is_equal_to(lint_path.config)
        assert_that(dict(resolved.sources)).is_equal_to(dict(lint_path.sources))
    assert_that(doctor).is_equal_to(lint_path.config)
    assert_that(lint_path.source_of("provider")).is_equal_to(ConfigSource.CONFIG)
    assert_that(lint_path.source_of("max_cost_usd")).is_equal_to(ConfigSource.CONFIG)


def test_an_env_overlay_reaches_every_surface_identically(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A ``LINTRO_AI_*`` overlay cannot be seen by one surface and not another.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Stand-in MCP workspace root.
    """
    monkeypatch.setenv("LINTRO_AI_MODEL", "claude-opus-4-20250514")
    lintro_config = LintroConfig(ai=dict(RAW_AI_SECTION))

    lint_path = interface_resolve(lintro_config)
    review_cli = resolve_effective_ai_config(lintro_config.ai)
    mcp = _mcp_resolved(monkeypatch, tmp_path)

    for resolved in (lint_path, review_cli, mcp):
        assert_that(resolved.config.model).is_equal_to("claude-opus-4-20250514")
        assert_that(resolved.source_of("model")).is_equal_to(ConfigSource.ENV)


def test_the_lint_transport_flag_is_an_overlay_with_provenance() -> None:
    """``check --transport cli`` resolves exactly like ``review --transport cli``.

    Before #2299 the lint path applied the flag with a post-resolution
    ``model_copy``, so it carried no provenance and could not be compared
    with the review path at all.
    """
    lintro_config = LintroConfig(ai=dict(RAW_AI_SECTION))
    overrides = AICliOverrides(transport="cli")

    lint_path = interface_resolve(lintro_config, cli_overrides=overrides)
    review_cli = resolve_effective_ai_config(
        lintro_config.ai,
        cli_overrides=overrides,
    )

    assert_that(lint_path.config).is_equal_to(review_cli.config)
    assert_that(lint_path.config.transport).is_equal_to(AITransport.CLI)
    assert_that(lint_path.source_of("transport")).is_equal_to(ConfigSource.FLAG)


def test_status_rendering_consumes_the_resolved_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The status renderer reports the resolved config, never a re-parse.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    from lintro.ai.display.status import render_ai_status as render_resolved
    from lintro.ai.interface import render_ai_status as render_facade

    monkeypatch.setattr(
        "lintro.ai.availability.is_provider_available",
        lambda _provider: True,
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    resolved = resolve_effective_ai_config(RAW_AI_SECTION, diagnostics=False)

    assert_that(render_facade(ai_config=dict(RAW_AI_SECTION), is_ci=False)).is_equal_to(
        render_resolved(ai_config=resolved, is_ci=False),
    )


# ---------------------------------------------------------------------------
# Cost-cap semantics (ADR-0008 invariant 6)
# ---------------------------------------------------------------------------


def test_a_cli_flag_may_raise_the_project_cost_cap() -> None:
    """``--max-cost-usd`` above the project value is accepted, not rejected.

    ADR-0008 invariant 6 (correcting this issue's original acceptance
    criterion) records CLI/env cap monotonicity as *not* a rule: the caps a
    user raises on their own invocation are theirs to raise. Only MCP's
    per-call argument clamps.
    """
    resolved = resolve_effective_ai_config(
        RAW_AI_SECTION,
        cli_overrides=AICliOverrides(max_cost_usd="25.0"),
    )

    assert_that(resolved.config.max_cost_usd).is_equal_to(25.0)
    assert_that(resolved.source_of("max_cost_usd")).is_equal_to(ConfigSource.FLAG)


def test_a_cli_flag_may_lift_the_cost_cap_entirely() -> None:
    """``--max-cost-usd uncapped`` lifts a project ceiling (#2024 / #2154)."""
    resolved = resolve_effective_ai_config(
        RAW_AI_SECTION,
        cli_overrides=AICliOverrides(max_cost_usd="uncapped"),
    )

    assert_that(resolved.config.max_cost_usd).is_none()
    assert_that(resolved.source_of("max_cost_usd")).is_equal_to(ConfigSource.FLAG)


def test_an_env_overlay_may_raise_the_project_cost_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``LINTRO_AI_MAX_COST_USD`` above the project value is accepted too.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("LINTRO_AI_MAX_COST_USD", "9.5")

    resolved = resolve_effective_ai_config(RAW_AI_SECTION)

    assert_that(resolved.config.max_cost_usd).is_equal_to(9.5)
    assert_that(resolved.source_of("max_cost_usd")).is_equal_to(ConfigSource.ENV)


def test_the_mcp_per_call_cap_argument_is_a_monotonic_clamp() -> None:
    """MCP may lower the resolved ceiling for one call, never raise it."""
    configured = resolve_effective_ai_config(RAW_AI_SECTION).config.max_cost_usd

    raised = resolve_budget_policy(requested=25.0, configured=configured)
    lowered = resolve_budget_policy(requested=0.25, configured=configured)
    absent = resolve_budget_policy(requested=None, configured=configured)

    assert_that(raised.effective_usd).is_equal_to(configured)
    assert_that(raised.clamped).is_true()
    assert_that(lowered.effective_usd).is_equal_to(0.25)
    assert_that(lowered.clamped).is_false()
    assert_that(absent.effective_usd).is_equal_to(configured)
