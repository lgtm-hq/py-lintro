"""Tests for BaseToolPlugin.reset_options() — B2 singleton state leak fix."""

from __future__ import annotations

from typing import TYPE_CHECKING

from assertpy import assert_that

if TYPE_CHECKING:
    from tests.unit.plugins.conftest import FakeToolPlugin


def test_reset_options_restores_defaults(fake_tool_plugin: FakeToolPlugin) -> None:
    """Verify reset_options clears accumulated state from set_options."""
    # Mutate state via set_options
    fake_tool_plugin.set_options(exclude_patterns=["*.pyc", "*.pyo"])
    fake_tool_plugin.set_options(include_venv=True)
    fake_tool_plugin.set_options(custom_flag="on")

    assert_that(fake_tool_plugin.include_venv).is_true()
    assert_that(fake_tool_plugin.options).contains_key("custom_flag")

    # Reset
    fake_tool_plugin.reset_options()

    assert_that(fake_tool_plugin.include_venv).is_false()
    assert_that(fake_tool_plugin.options).does_not_contain_key("custom_flag")
    # Default timeout should be restored
    assert_that(fake_tool_plugin.options.get("timeout")).is_equal_to(
        fake_tool_plugin.definition.default_timeout,
    )


def test_reset_options_clears_exclude_patterns(
    fake_tool_plugin: FakeToolPlugin,
) -> None:
    """Verify reset_options resets exclude_patterns to defaults."""
    list(fake_tool_plugin.exclude_patterns)
    fake_tool_plugin.set_options(exclude_patterns=["custom_pattern_*"])

    assert_that(fake_tool_plugin.exclude_patterns).contains("custom_pattern_*")

    fake_tool_plugin.reset_options()

    # After reset, should have only the default patterns (from _setup_defaults)
    assert_that(fake_tool_plugin.exclude_patterns).does_not_contain("custom_pattern_*")


def test_reset_options_allows_clean_reconfiguration(
    fake_tool_plugin: FakeToolPlugin,
) -> None:
    """Verify that set_options after reset_options does not accumulate prior state."""
    fake_tool_plugin.set_options(exclude_patterns=["first_*"])
    fake_tool_plugin.reset_options()
    fake_tool_plugin.set_options(exclude_patterns=["second_*"])

    assert_that(fake_tool_plugin.exclude_patterns).contains("second_*")
    assert_that(fake_tool_plugin.exclude_patterns).does_not_contain("first_*")
