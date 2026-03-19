"""Tests for BaseToolPlugin.reset_options() — B2 singleton state leak fix."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

from assertpy import assert_that

if TYPE_CHECKING:
    from tests.unit.plugins.conftest import FakeToolPlugin


def test_reset_options_restores_defaults(fake_tool_plugin: FakeToolPlugin) -> None:
    """Verify reset_options clears accumulated state from set_options."""
    default_timeout = fake_tool_plugin.definition.default_timeout

    # Mutate state via set_options
    fake_tool_plugin.set_options(exclude_patterns=["*.pyc", "*.pyo"])
    fake_tool_plugin.set_options(include_venv=True)
    fake_tool_plugin.set_options(custom_flag="on")
    fake_tool_plugin.set_options(timeout=default_timeout + 60)

    assert_that(fake_tool_plugin.include_venv).is_true()
    assert_that(fake_tool_plugin.options).contains_key("custom_flag")
    assert_that(fake_tool_plugin.options["timeout"]).is_equal_to(default_timeout + 60)

    # Reset
    fake_tool_plugin.reset_options()

    assert_that(fake_tool_plugin.include_venv).is_false()
    assert_that(fake_tool_plugin.options).does_not_contain_key("custom_flag")
    assert_that(fake_tool_plugin.options.get("timeout")).is_equal_to(default_timeout)


def test_reset_options_clears_exclude_patterns(
    fake_tool_plugin: FakeToolPlugin,
) -> None:
    """Verify reset_options resets exclude_patterns to defaults."""
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


def test_bandit_reset_options_preserves_native_config() -> None:
    """Verify BanditPlugin.reset_options() re-applies native pyproject.toml config."""
    native_config = {
        "skips": ["B101", "B601"],
        "tests": ["B201"],
        "severity": "HIGH",
    }

    with patch(
        "lintro.tools.definitions.bandit.load_bandit_config",
        return_value=native_config,
    ):
        from lintro.tools.definitions.bandit import BanditPlugin

        plugin = BanditPlugin()

    # Native config should be applied from __post_init__
    assert_that(plugin.options["skips"]).is_equal_to("B101,B601")
    assert_that(plugin.options["tests"]).is_equal_to("B201")
    assert_that(plugin.options["severity"]).is_equal_to("HIGH")

    # Override all native options with user values
    plugin.set_options(skips="B102", tests="B999", severity="LOW")
    assert_that(plugin.options["skips"]).is_equal_to("B102")
    assert_that(plugin.options["tests"]).is_equal_to("B999")
    assert_that(plugin.options["severity"]).is_equal_to("LOW")

    # Reset should restore native config, not defaults (which have skips=None)
    with patch(
        "lintro.tools.definitions.bandit.load_bandit_config",
        return_value=native_config,
    ):
        plugin.reset_options()

    assert_that(plugin.options["skips"]).is_equal_to("B101,B601")
    assert_that(plugin.options["tests"]).is_equal_to("B201")
    assert_that(plugin.options["severity"]).is_equal_to("HIGH")
