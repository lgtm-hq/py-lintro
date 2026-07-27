"""Unit tests for pre-execution summary AI rendering.

The AI lines themselves are rendered by the AI layer (issue #724 PR 2); the
summary only places pre-rendered lines in the table. These tests feed it the
real AI renderer output so the end-to-end text stays pinned.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from assertpy import assert_that
from rich.console import Console

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.interface import render_ai_status
from lintro.ai.registry import AIProvider
from lintro.utils.console.pre_execution_summary import print_pre_execution_summary


def _render_summary(
    ai_config: AIConfig | None,
    *,
    use_renderer: bool = True,
) -> str:
    """Render the pre-execution summary and return plain text output.

    Args:
        ai_config: AI configuration passed to the AI status renderer.
        use_renderer: When False, no AI lines are supplied at all, mimicking a
            caller that did not inject an AI status renderer.

    Returns:
        The recorded console text.
    """
    console = Console(record=True, force_terminal=False, width=160)
    ai_status_lines = (
        render_ai_status(ai_config=ai_config, is_ci=False) if use_renderer else None
    )
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
            ai_status_lines=ai_status_lines,
        )
    return console.export_text()


def test_pre_execution_summary_shows_ai_when_disabled() -> None:
    """AI row should still be displayed when AI features are disabled."""
    output = _render_summary(
        AIConfig(
            enabled=False,
            provider=AIProvider.OPENAI,
            max_parallel_calls=7,
        ),
    )

    assert_that(output).contains("AI")
    assert_that(output).contains("disabled")
    # When disabled, config details should not be shown
    assert_that(output).does_not_contain("provider: openai")
    assert_that(output).does_not_contain("parallel: 7 workers")


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
            transport=AITransport.API,
            provider=AIProvider.ANTHROPIC,
            max_parallel_calls=3,
        ),
    )

    assert_that(output).contains("AI")
    assert_that(output).contains("enabled")
    assert_that(output).contains("provider: anthropic")
    assert_that(output).contains("parallel: 3 workers")
    assert_that(output).contains("safe-auto-apply: on")
    assert_that(output).contains("verify-fixes: off")


def test_pre_execution_summary_shows_ai_when_config_missing() -> None:
    """AI row should still be shown when no AI config object is passed."""
    output = _render_summary(ai_config=None)

    assert_that(output).contains("AI")
    assert_that(output).contains("disabled (no config)")


def test_pre_execution_summary_falls_back_without_ai_lines() -> None:
    """Omitting the AI lines renders the same row as a missing config."""
    with_renderer = _render_summary(ai_config=None)
    without_renderer = _render_summary(ai_config=None, use_renderer=False)

    assert_that(without_renderer).is_equal_to(with_renderer)
