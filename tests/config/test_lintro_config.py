"""Tests for LintroConfig dataclasses."""

from assertpy import assert_that

from lintro.config.lintro_config import (
    EnforceConfig,
    ExecutionConfig,
    LintroConfig,
    LintroToolConfig,
)


def test_default_values() -> None:
    """EnforceConfig should have None defaults."""
    config = EnforceConfig()

    assert_that(config.line_length).is_none()
    assert_that(config.target_python).is_none()


def test_execution_config_defaults() -> None:
    """ExecutionConfig should have sensible defaults."""
    config = ExecutionConfig()

    assert_that(config.enabled_tools).is_equal_to([])
    assert_that(config.fail_fast).is_false()
    assert_that(config.parallel).is_true()


def test_tool_config_defaults() -> None:
    """LintroToolConfig should have sensible defaults."""
    config = LintroToolConfig()

    assert_that(config.enabled).is_true()
    assert_that(config.config_source).is_none()


def test_lintro_config_defaults() -> None:
    """LintroConfig should have sensible defaults."""
    config = LintroConfig()

    assert_that(config.enforce).is_not_none()
    assert_that(config.execution).is_not_none()
    assert_that(config.defaults).is_equal_to({})
    assert_that(config.tools).is_equal_to({})
    assert_that(config.config_path).is_none()


def test_get_tool_config_returns_default() -> None:
    """get_tool_config should return default for unknown tools."""
    config = LintroConfig()

    tool_config = config.get_tool_config("unknown_tool")

    assert_that(tool_config.enabled).is_true()
    assert_that(tool_config.config_source).is_none()


def test_get_tool_config_case_insensitive() -> None:
    """get_tool_config should be case insensitive."""
    config = LintroConfig(
        tools={"ruff": LintroToolConfig(enabled=False)},
    )

    # Lowercase should work
    assert_that(config.get_tool_config("ruff").enabled).is_false()
    # Uppercase should also work (converted to lowercase)
    assert_that(config.get_tool_config("RUFF").enabled).is_false()
    # Mixed case should also work
    assert_that(config.get_tool_config("Ruff").enabled).is_false()


def test_is_tool_enabled_filtered() -> None:
    """is_tool_enabled should filter by enabled_tools."""
    config = LintroConfig(
        execution=ExecutionConfig(enabled_tools=["ruff"]),
    )

    assert_that(config.is_tool_enabled("ruff")).is_true()
    assert_that(config.is_tool_enabled("prettier")).is_false()


def test_get_tool_defaults() -> None:
    """get_tool_defaults should return defaults for a tool."""
    config = LintroConfig(
        defaults={
            "prettier": {"singleQuote": True, "tabWidth": 2},
        },
    )

    defaults = config.get_tool_defaults("prettier")

    assert_that(defaults["singleQuote"]).is_true()
    assert_that(defaults["tabWidth"]).is_equal_to(2)


def test_get_effective_line_length_from_enforce() -> None:
    """get_effective_line_length should use enforce setting."""
    config = LintroConfig(
        enforce=EnforceConfig(line_length=120),
    )

    assert_that(config.get_effective_line_length("ruff")).is_equal_to(120)
    assert_that(config.get_effective_line_length("prettier")).is_equal_to(120)


def test_get_effective_target_python() -> None:
    """get_effective_target_python should use enforce setting."""
    config = LintroConfig(
        enforce=EnforceConfig(target_python="py312"),
    )

    assert_that(config.get_effective_target_python("ruff")).is_equal_to("py312")
    assert_that(config.get_effective_target_python("black")).is_equal_to("py312")
