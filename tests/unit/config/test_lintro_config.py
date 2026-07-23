"""Tests for lintro.config.lintro_config module."""

from __future__ import annotations

from assertpy import assert_that

from lintro.config.enforce_config import EnforceConfig
from lintro.config.execution_config import ExecutionConfig
from lintro.config.lintro_config import LintroConfig
from lintro.config.tool_config import LintroToolConfig


def test_lintro_config_default_execution() -> None:
    """LintroConfig has default ExecutionConfig."""
    config = LintroConfig()
    assert_that(config.execution).is_instance_of(ExecutionConfig)


def test_lintro_config_default_enforce() -> None:
    """LintroConfig has default EnforceConfig."""
    config = LintroConfig()
    assert_that(config.enforce).is_instance_of(EnforceConfig)


def test_lintro_config_default_defaults() -> None:
    """LintroConfig has empty defaults dict."""
    config = LintroConfig()
    assert_that(config.defaults).is_empty()


def test_lintro_config_default_tools() -> None:
    """LintroConfig has empty tools dict."""
    config = LintroConfig()
    assert_that(config.tools).is_empty()


def test_lintro_config_default_config_path() -> None:
    """LintroConfig has None config_path by default."""
    config = LintroConfig()
    assert_that(config.config_path).is_none()


def test_lintro_config_set_config_path() -> None:
    """LintroConfig accepts config_path."""
    config = LintroConfig(config_path="/path/to/.lintro-config.yaml")
    assert_that(config.config_path).is_equal_to("/path/to/.lintro-config.yaml")


def test_get_tool_config_returns_configured_tool() -> None:
    """get_tool_config returns configured tool config."""
    tool_config = LintroToolConfig(enabled=False)
    config = LintroConfig(tools={"ruff": tool_config})
    result = config.get_tool_config("ruff")
    assert_that(result.enabled).is_false()


def test_get_tool_config_returns_default_for_missing() -> None:
    """get_tool_config returns default config for unconfigured tool."""
    config = LintroConfig()
    result = config.get_tool_config("ruff")
    assert_that(result).is_instance_of(LintroToolConfig)
    assert_that(result.enabled).is_true()


def test_get_tool_config_case_insensitive() -> None:
    """get_tool_config is case-insensitive."""
    tool_config = LintroToolConfig(enabled=False)
    config = LintroConfig(tools={"ruff": tool_config})
    result = config.get_tool_config("RUFF")
    assert_that(result.enabled).is_false()


def test_get_tool_config_matches_hyphen_config_for_underscore_tool() -> None:
    """get_tool_config accepts hyphenated config for underscored tools."""
    tool_config = LintroToolConfig(enabled=False)
    config = LintroConfig(tools={"html-validate": tool_config})
    result = config.get_tool_config("html_validate")
    assert_that(result.enabled).is_false()


def test_get_tool_config_matches_underscore_config_for_hyphen_tool() -> None:
    """get_tool_config accepts underscored config for hyphenated tools."""
    tool_config = LintroToolConfig(enabled=False)
    config = LintroConfig(tools={"astro_check": tool_config})
    result = config.get_tool_config("astro-check")
    assert_that(result.enabled).is_false()


def test_is_tool_enabled_default_true() -> None:
    """is_tool_enabled returns True for unconfigured tool."""
    config = LintroConfig()
    assert_that(config.is_tool_enabled("ruff")).is_true()


def test_is_tool_enabled_respects_tool_config() -> None:
    """is_tool_enabled respects tool enabled flag."""
    tool_config = LintroToolConfig(enabled=False)
    config = LintroConfig(tools={"ruff": tool_config})
    assert_that(config.is_tool_enabled("ruff")).is_false()


def test_is_tool_enabled_respects_enabled_tools_filter() -> None:
    """is_tool_enabled respects execution.enabled_tools filter."""
    execution = ExecutionConfig(enabled_tools=["black", "mypy"])
    config = LintroConfig(execution=execution)
    assert_that(config.is_tool_enabled("ruff")).is_false()
    assert_that(config.is_tool_enabled("black")).is_true()


def test_is_tool_enabled_empty_enabled_tools_allows_all() -> None:
    """is_tool_enabled allows all tools when enabled_tools is empty."""
    config = LintroConfig()
    assert_that(config.is_tool_enabled("ruff")).is_true()
    assert_that(config.is_tool_enabled("black")).is_true()
    assert_that(config.is_tool_enabled("mypy")).is_true()


def test_is_tool_enabled_case_insensitive() -> None:
    """is_tool_enabled is case-insensitive."""
    execution = ExecutionConfig(enabled_tools=["RUFF"])
    config = LintroConfig(execution=execution)
    assert_that(config.is_tool_enabled("ruff")).is_true()
    assert_that(config.is_tool_enabled("Ruff")).is_true()


def test_is_tool_enabled_matches_enabled_tools_aliases() -> None:
    """execution.enabled_tools accepts hyphen/underscore spelling aliases."""
    execution = ExecutionConfig(enabled_tools=["html-validate"])
    config = LintroConfig(execution=execution)
    assert_that(config.is_tool_enabled("html_validate")).is_true()
    assert_that(config.is_tool_enabled("ruff")).is_false()


def test_is_tool_enabled_matches_tool_config_aliases() -> None:
    """tools.<name>.enabled applies across hyphen/underscore aliases."""
    config = LintroConfig(
        tools={"html-validate": LintroToolConfig(enabled=False)},
    )
    assert_that(config.is_tool_enabled("html_validate")).is_false()


def test_get_tool_defaults_returns_configured_defaults() -> None:
    """get_tool_defaults returns configured defaults."""
    config = LintroConfig(defaults={"ruff": {"line-length": 120}})
    result = config.get_tool_defaults("ruff")
    assert_that(result).is_equal_to({"line-length": 120})


def test_get_tool_defaults_returns_empty_for_missing() -> None:
    """get_tool_defaults returns empty dict for unconfigured tool."""
    config = LintroConfig()
    result = config.get_tool_defaults("ruff")
    assert_that(result).is_empty()


def test_get_tool_defaults_case_insensitive() -> None:
    """get_tool_defaults is case-insensitive."""
    config = LintroConfig(defaults={"ruff": {"line-length": 88}})
    result = config.get_tool_defaults("RUFF")
    assert_that(result).is_equal_to({"line-length": 88})


def test_get_effective_line_length_returns_enforced() -> None:
    """get_effective_line_length returns enforce.line_length."""
    enforce = EnforceConfig(line_length=120)
    config = LintroConfig(enforce=enforce)
    result = config.get_effective_line_length("ruff")
    assert_that(result).is_equal_to(120)


def test_get_effective_line_length_returns_none_when_not_set() -> None:
    """get_effective_line_length returns None when not configured."""
    config = LintroConfig()
    result = config.get_effective_line_length("ruff")
    assert_that(result).is_none()


def test_get_effective_target_python_returns_enforced() -> None:
    """get_effective_target_python returns enforce.target_python."""
    enforce = EnforceConfig(target_python="py311")
    config = LintroConfig(enforce=enforce)
    result = config.get_effective_target_python("ruff")
    assert_that(result).is_equal_to("py311")


def test_get_effective_target_python_returns_none_when_not_set() -> None:
    """get_effective_target_python returns None when not configured."""
    config = LintroConfig()
    result = config.get_effective_target_python("ruff")
    assert_that(result).is_none()
