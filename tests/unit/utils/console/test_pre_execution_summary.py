"""Unit tests for pre-execution summary AI rendering."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from assertpy import assert_that
from rich.console import Console

from lintro.ai.config import AIConfig
from lintro.utils.console.pre_execution_summary import print_pre_execution_summary


def _render_summary(ai_config: AIConfig | None) -> str:
    """Render the pre-execution summary and return plain text output."""
    console = Console(record=True, force_terminal=False, width=160)
    with patch(
        "lintro.utils.console.pre_execution_summary.Console",
        return_value=console,
    ):
        print_pre_execution_summary(
            tools_to_run=["ruff"],
            skipped_tools=[],
            effective_auto_install=True,
            is_container=False,
            is_ci=False,
            per_tool_auto_install=None,
            ai_config=ai_config,
        )
    return console.export_text()


def test_pre_execution_summary_shows_ai_when_disabled() -> None:
    """AI row should still be displayed when AI features are disabled."""
    output = _render_summary(
        AIConfig(
            enabled=False,
            provider="openai",
            max_parallel_calls=7,
        ),
    )

    assert_that(output).contains("AI")
    assert_that(output).contains("disabled")
    assert_that(output).contains("provider: openai")
    assert_that(output).contains("parallel: 7 workers")
    assert_that(output).contains("safe-auto-apply: on")
    assert_that(output).contains("validate-after-group: off")


def test_pre_execution_summary_shows_ai_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enabled AI row should include healthy status and details."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        "lintro.ai.availability.is_provider_available",
        lambda _provider: True,
    )

    output = _render_summary(
        AIConfig(
            enabled=True,
            provider="anthropic",
            max_parallel_calls=3,
        ),
    )

    assert_that(output).contains("AI")
    assert_that(output).contains("enabled")
    assert_that(output).contains("provider: anthropic")
    assert_that(output).contains("parallel: 3 workers")
    assert_that(output).contains("safe-auto-apply: on")
    assert_that(output).contains("validate-after-group: off")


def test_pre_execution_summary_shows_ai_when_config_missing() -> None:
    """AI row should still be shown when no AI config object is passed."""
    output = _render_summary(ai_config=None)

    assert_that(output).contains("AI")
    assert_that(output).contains("disabled (no config)")
