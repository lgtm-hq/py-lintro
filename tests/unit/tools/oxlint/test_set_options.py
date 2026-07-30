"""Tests for OxlintPlugin.set_options() method."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

if TYPE_CHECKING:
    from lintro.tools.definitions.oxlint import OxlintPlugin


# =============================================================================
# Tests for config and tsconfig options validation
# =============================================================================


def test_config_accepts_string(oxlint_plugin: OxlintPlugin) -> None:
    """Config option accepts a string value.

    Args:
        oxlint_plugin: The OxlintPlugin instance to test.
    """
    oxlint_plugin.set_options(config=".oxlintrc.custom.json")
    assert_that(oxlint_plugin.options.get("config")).is_equal_to(
        ".oxlintrc.custom.json",
    )


def test_config_rejects_non_string(oxlint_plugin: OxlintPlugin) -> None:
    """Config option rejects non-string values.

    Args:
        oxlint_plugin: The OxlintPlugin instance to test.
    """
    with pytest.raises(ValueError, match="config must be a string"):
        oxlint_plugin.set_options(config=123)  # type: ignore[arg-type]


def test_tsconfig_accepts_string(oxlint_plugin: OxlintPlugin) -> None:
    """Tsconfig option accepts a string value.

    Args:
        oxlint_plugin: The OxlintPlugin instance to test.
    """
    oxlint_plugin.set_options(tsconfig="tsconfig.app.json")
    assert_that(oxlint_plugin.options.get("tsconfig")).is_equal_to(
        "tsconfig.app.json",
    )


def test_tsconfig_rejects_non_string(oxlint_plugin: OxlintPlugin) -> None:
    """Tsconfig option rejects non-string values.

    Args:
        oxlint_plugin: The OxlintPlugin instance to test.
    """
    with pytest.raises(ValueError, match="tsconfig must be a string"):
        oxlint_plugin.set_options(tsconfig=True)  # type: ignore[arg-type]


# =============================================================================
# Tests for the type_aware option validation
# =============================================================================


def test_type_aware_accepts_bool(oxlint_plugin: OxlintPlugin) -> None:
    """type_aware option accepts a boolean value.

    Args:
        oxlint_plugin: The OxlintPlugin instance to test.
    """
    oxlint_plugin.set_options(type_aware=True)
    assert_that(oxlint_plugin.options.get("type_aware")).is_true()


def test_type_aware_rejects_non_bool(oxlint_plugin: OxlintPlugin) -> None:
    """type_aware option rejects non-boolean values.

    Args:
        oxlint_plugin: The OxlintPlugin instance to test.
    """
    with pytest.raises(ValueError, match="type_aware must be a boolean"):
        oxlint_plugin.set_options(type_aware="yes")  # type: ignore[arg-type]


# =============================================================================
# Tests for rule list options (allow, deny, warn)
# =============================================================================


@pytest.mark.parametrize(
    ("option_name", "value", "expected"),
    [
        ("allow", ["no-console", "no-unused-vars"], ["no-console", "no-unused-vars"]),
        ("allow", "no-console", ["no-console"]),
        ("deny", ["no-debugger", "eqeqeq"], ["no-debugger", "eqeqeq"]),
        ("deny", "no-debugger", ["no-debugger"]),
        ("warn", ["no-console", "complexity"], ["no-console", "complexity"]),
        ("warn", "no-console", ["no-console"]),
    ],
    ids=[
        "allow_accepts_list",
        "allow_accepts_string",
        "deny_accepts_list",
        "deny_accepts_string",
        "warn_accepts_list",
        "warn_accepts_string",
    ],
)
def test_rule_option_accepts_valid_input(
    oxlint_plugin: OxlintPlugin,
    option_name: str,
    value: list[str] | str,
    expected: list[str],
) -> None:
    """Rule options accept lists and strings.

    Args:
        oxlint_plugin: The OxlintPlugin instance to test.
        option_name: The name of the option to set.
        value: The value to set.
        expected: The expected normalized value.
    """
    # Mypy can't infer types from parametrized kwargs
    oxlint_plugin.set_options(**{option_name: value})  # type: ignore[arg-type]
    assert_that(oxlint_plugin.options.get(option_name)).is_equal_to(expected)


def test_rule_list_rejects_invalid_type(oxlint_plugin: OxlintPlugin) -> None:
    """Rule list options reject invalid types.

    Args:
        oxlint_plugin: The OxlintPlugin instance to test.
    """
    with pytest.raises(ValueError, match="allow must be a string or list"):
        # Intentionally passing wrong type to test validation
        oxlint_plugin.set_options(allow=123)  # type: ignore[arg-type]


# =============================================================================
# Tests for setting multiple options
# =============================================================================


def test_set_multiple_options(oxlint_plugin: OxlintPlugin) -> None:
    """Multiple options can be set in a single call.

    Args:
        oxlint_plugin: The OxlintPlugin instance to test.
    """
    oxlint_plugin.set_options(
        config=".oxlintrc.json",
        tsconfig="tsconfig.json",
        quiet=True,
        deny=["no-debugger"],
        warn=["no-console"],
    )

    assert_that(oxlint_plugin.options.get("config")).is_equal_to(".oxlintrc.json")
    assert_that(oxlint_plugin.options.get("tsconfig")).is_equal_to("tsconfig.json")
    assert_that(oxlint_plugin.options.get("quiet")).is_true()
    assert_that(oxlint_plugin.options.get("deny")).is_equal_to(["no-debugger"])
    assert_that(oxlint_plugin.options.get("warn")).is_equal_to(["no-console"])


def test_none_values_do_not_override(oxlint_plugin: OxlintPlugin) -> None:
    """None values do not override previously set options.

    Args:
        oxlint_plugin: The OxlintPlugin instance to test.
    """
    oxlint_plugin.set_options(config=".oxlintrc.json")
    oxlint_plugin.set_options(config=None, tsconfig="tsconfig.json")

    # config should still be set from first call
    assert_that(oxlint_plugin.options.get("config")).is_equal_to(".oxlintrc.json")
    assert_that(oxlint_plugin.options.get("tsconfig")).is_equal_to("tsconfig.json")


# =============================================================================
# Tests for _build_oxlint_args() helper method
# =============================================================================


def test_build_args_empty_options(oxlint_plugin: OxlintPlugin) -> None:
    """Empty options returns empty args list.

    Args:
        oxlint_plugin: The OxlintPlugin instance to test.
    """
    args = oxlint_plugin._build_oxlint_args(oxlint_plugin.options)
    assert_that(args).is_empty()


def test_build_args_config_adds_flag(oxlint_plugin: OxlintPlugin) -> None:
    """Config option adds --config flag.

    Args:
        oxlint_plugin: The OxlintPlugin instance to test.
    """
    oxlint_plugin.set_options(config=".oxlintrc.json")
    args = oxlint_plugin._build_oxlint_args(oxlint_plugin.options)
    assert_that(args).contains("--config", ".oxlintrc.json")


def test_build_args_tsconfig_adds_flag(oxlint_plugin: OxlintPlugin) -> None:
    """Tsconfig option adds --tsconfig flag.

    Args:
        oxlint_plugin: The OxlintPlugin instance to test.
    """
    oxlint_plugin.set_options(tsconfig="tsconfig.json")
    args = oxlint_plugin._build_oxlint_args(oxlint_plugin.options)
    assert_that(args).contains("--tsconfig", "tsconfig.json")


@pytest.mark.parametrize(
    ("option_name", "flag", "rules"),
    [
        ("allow", "--allow", ["no-console", "no-unused-vars"]),
        ("deny", "--deny", ["no-debugger", "eqeqeq"]),
        ("warn", "--warn", ["complexity"]),
    ],
    ids=["allow_adds_flags", "deny_adds_flags", "warn_adds_flags"],
)
def test_build_args_rule_options_add_flags(
    oxlint_plugin: OxlintPlugin,
    option_name: str,
    flag: str,
    rules: list[str],
) -> None:
    """Rule options add flags for each rule.

    Args:
        oxlint_plugin: The OxlintPlugin instance to test.
        option_name: The option name to set.
        flag: The expected CLI flag.
        rules: The rules to set.
    """
    # Mypy can't infer types from parametrized kwargs
    oxlint_plugin.set_options(**{option_name: rules})  # type: ignore[arg-type]
    args = oxlint_plugin._build_oxlint_args(oxlint_plugin.options)
    for rule in rules:
        assert_that(args).contains(flag, rule)


def test_build_args_type_aware_adds_flag(oxlint_plugin: OxlintPlugin) -> None:
    """type_aware=True adds the --type-aware flag.

    Args:
        oxlint_plugin: The OxlintPlugin instance to test.
    """
    oxlint_plugin.set_options(type_aware=True)
    args = oxlint_plugin._build_oxlint_args(oxlint_plugin.options)
    assert_that(args).contains("--type-aware")


def test_build_args_type_aware_absent_by_default(oxlint_plugin: OxlintPlugin) -> None:
    """--type-aware is absent when type_aware is not set.

    Args:
        oxlint_plugin: The OxlintPlugin instance to test.
    """
    args = oxlint_plugin._build_oxlint_args(oxlint_plugin.options)
    assert_that(args).does_not_contain("--type-aware")


def test_build_args_type_aware_false_omits_flag(oxlint_plugin: OxlintPlugin) -> None:
    """--type-aware is omitted when type_aware is explicitly False.

    Args:
        oxlint_plugin: The OxlintPlugin instance to test.
    """
    oxlint_plugin.set_options(type_aware=False)
    args = oxlint_plugin._build_oxlint_args(oxlint_plugin.options)
    assert_that(args).does_not_contain("--type-aware")


def test_build_args_multiple_options_combine(oxlint_plugin: OxlintPlugin) -> None:
    """Multiple options combine into a single args list.

    Args:
        oxlint_plugin: The OxlintPlugin instance to test.
    """
    oxlint_plugin.set_options(
        config=".oxlintrc.json",
        deny=["no-debugger"],
        allow=["no-console"],
    )
    args = oxlint_plugin._build_oxlint_args(oxlint_plugin.options)

    assert_that(args).contains("--config", ".oxlintrc.json")
    assert_that(args).contains("--deny", "no-debugger")
    assert_that(args).contains("--allow", "no-console")
