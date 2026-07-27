"""Parity tests for the relocated pre-execution AI status renderer.

The rendering moved out of ``lintro.utils.console.pre_execution_summary`` into
the AI package (issue #724 PR 2). These tests pin the exact lines for every
branch so the move is provably output-preserving.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.display.status import render_ai_status
from lintro.ai.enums import AITransport
from lintro.ai.registry import AIProvider


def _stub_config(**overrides: object) -> AIConfig:
    """Build a duck-typed AI config for branch coverage.

    A namespace stands in for :class:`AIConfig` so branches the real model
    rejects (an unknown provider name) stay reachable.

    Args:
        **overrides: Attribute overrides applied on top of the defaults.

    Returns:
        An object exposing the attributes ``render_ai_status`` reads.
    """
    defaults: dict[str, object] = {
        "enabled": True,
        "provider": "anthropic",
        "api_key_env": "",
        "model": "",
        "auto_apply": False,
        "max_parallel_calls": 4,
        "auto_apply_safe_fixes": True,
        "validate_after_group": False,
    }
    defaults.update(overrides)
    return cast("AIConfig", SimpleNamespace(**defaults))


def test_render_ai_status_no_config() -> None:
    """A missing config renders the single no-config line."""
    assert_that(render_ai_status(ai_config=None, is_ci=False)).is_equal_to(
        ["[dim]disabled (no config)[/dim]"],
    )


def test_render_ai_status_disabled() -> None:
    """A disabled config renders the single disabled line."""
    ai_config = AIConfig(enabled=False, transport=AITransport.API)

    assert_that(render_ai_status(ai_config=ai_config, is_ci=False)).is_equal_to(
        ["[dim]disabled[/dim]"],
    )


def test_render_ai_status_unknown_provider() -> None:
    """An unsupported provider renders the red unknown-provider lines."""
    lines = render_ai_status(
        ai_config=_stub_config(provider="not-a-provider"),
        is_ci=False,
    )

    assert_that(lines[0]).is_equal_to("[red]enabled (unknown provider)[/red]")
    assert_that(lines[1]).contains("'not-a-provider' is not supported. Use: ")
    assert_that(lines[1]).contains(sorted(set(AIProvider))[0])
    assert_that(lines[2]).is_equal_to("  provider: not-a-provider")


def test_render_ai_status_sdk_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing SDK renders the install hint."""
    monkeypatch.setattr(
        "lintro.ai.availability.is_provider_available",
        lambda _provider: False,
    )

    lines = render_ai_status(ai_config=_stub_config(), is_ci=False)

    assert_that(lines[0]).is_equal_to("[red]enabled (SDK not installed)[/red]")
    assert_that(lines[1]).is_equal_to(
        "  [yellow]run: uv pip install 'lintro\\[ai]'[/yellow]",
    )


def test_render_ai_status_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """An available SDK with no API key renders the key hint."""
    monkeypatch.setattr(
        "lintro.ai.availability.is_provider_available",
        lambda _provider: True,
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    lines = render_ai_status(ai_config=_stub_config(), is_ci=False)

    assert_that(lines[0]).is_equal_to("[yellow]enabled (API key missing)[/yellow]")
    expected = "  [yellow]set ANTHROPIC_API_KEY env var[/yellow]"
    assert_that(lines[1]).is_equal_to(expected)


def test_render_ai_status_fully_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """A healthy configuration renders status plus the settings block."""
    monkeypatch.setattr(
        "lintro.ai.availability.is_provider_available",
        lambda _provider: True,
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    lines = render_ai_status(
        ai_config=_stub_config(max_parallel_calls=3),
        is_ci=False,
    )

    assert_that(lines[0]).is_equal_to("[green]enabled[/green]")
    assert_that(lines).contains("  provider: anthropic")
    assert_that(lines).contains("  parallel: 3 workers")
    assert_that(lines).contains("  safe-auto-apply: [green]on[/green]")
    assert_that(lines).contains("  verify-fixes: [dim]off[/dim]")


def test_render_ai_status_auto_apply_warning_outside_ci(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outside CI, ``auto_apply`` renders the loud destructive warning."""
    monkeypatch.setattr(
        "lintro.ai.availability.is_provider_available",
        lambda _provider: True,
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    lines = render_ai_status(ai_config=_stub_config(auto_apply=True), is_ci=False)

    assert_that(lines).contains(
        "  auto-apply: [bold red]on (files will be "
        "modified without confirmation)[/bold red]",
    )


def test_render_ai_status_auto_apply_quiet_in_ci(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In CI, ``auto_apply`` renders the quiet green line."""
    monkeypatch.setattr(
        "lintro.ai.availability.is_provider_available",
        lambda _provider: True,
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    lines = render_ai_status(ai_config=_stub_config(auto_apply=True), is_ci=True)

    assert_that(lines).contains("  auto-apply: [green]on[/green]")


def test_render_ai_status_model_marks_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unset model is rendered with the ``(default)`` marker."""
    monkeypatch.setattr(
        "lintro.ai.availability.is_provider_available",
        lambda _provider: True,
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    default_lines = render_ai_status(ai_config=_stub_config(), is_ci=False)
    explicit_lines = render_ai_status(
        ai_config=_stub_config(model="some-model"),
        is_ci=False,
    )

    assert_that([line for line in default_lines if "model:" in line][0]).contains(
        "[dim](default)[/dim]",
    )
    assert_that(explicit_lines).contains("  model: some-model")


def test_render_ai_status_respects_custom_api_key_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured ``api_key_env`` is the variable that gets checked."""
    monkeypatch.setattr(
        "lintro.ai.availability.is_provider_available",
        lambda _provider: True,
    )
    monkeypatch.delenv("CUSTOM_AI_KEY", raising=False)

    lines = render_ai_status(
        ai_config=_stub_config(api_key_env="CUSTOM_AI_KEY"),
        is_ci=False,
    )

    assert_that(lines[1]).is_equal_to("  [yellow]set CUSTOM_AI_KEY env var[/yellow]")
