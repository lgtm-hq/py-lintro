"""Unit tests for djLint plugin definition, options, and command building."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.enums.tool_type import ToolType
from lintro.tools.definitions.djlint import (
    DJLINT_DEFAULT_PROFILE,
    DJLINT_DEFAULT_TIMEOUT,
    DjlintPlugin,
)

# --- Default option values ---


@pytest.mark.parametrize(
    ("option_name", "expected_value"),
    [
        ("timeout", DJLINT_DEFAULT_TIMEOUT),
        ("profile", DJLINT_DEFAULT_PROFILE),
        ("indent", None),
        ("max_line_length", None),
        ("ignore", None),
        ("extend_exclude", None),
    ],
    ids=[
        "timeout_default",
        "profile_jinja",
        "indent_none",
        "max_line_length_none",
        "ignore_none",
        "extend_exclude_none",
    ],
)
def test_default_options_values(
    djlint_plugin: DjlintPlugin,
    option_name: str,
    expected_value: object,
) -> None:
    """Default options have the expected values.

    Args:
        djlint_plugin: The DjlintPlugin instance to test.
        option_name: The option name to check.
        expected_value: The expected value for the option.
    """
    assert_that(
        djlint_plugin.definition.default_options[option_name],
    ).is_equal_to(expected_value)


# --- set_options: valid values ---


@pytest.mark.parametrize(
    ("option_name", "option_value"),
    [
        ("profile", "django"),
        ("profile", "handlebars"),
        ("profile", "nunjucks"),
        ("profile", "golang"),
        ("indent", 2),
        ("indent", 0),
        ("max_line_length", 120),
        ("ignore", "H014,H017"),
        ("extend_exclude", "vendor/"),
    ],
    ids=[
        "profile_django",
        "profile_handlebars",
        "profile_nunjucks",
        "profile_golang",
        "indent_two",
        "indent_zero",
        "max_line_length",
        "ignore_codes",
        "extend_exclude",
    ],
)
def test_set_options_valid(
    djlint_plugin: DjlintPlugin,
    option_name: str,
    option_value: object,
) -> None:
    """Valid options are stored correctly.

    Args:
        djlint_plugin: The DjlintPlugin instance to test.
        option_name: The option name to set.
        option_value: The value to set.
    """
    djlint_plugin.set_options(**{option_name: option_value})  # type: ignore[arg-type]
    assert_that(djlint_plugin.options.get(option_name)).is_equal_to(option_value)


# --- set_options: invalid types ---


@pytest.mark.parametrize(
    ("option_name", "invalid_value", "error_match"),
    [
        ("profile", 123, "profile must be a string"),
        ("indent", "two", "indent must be an integer"),
        ("indent", -1, "indent must be at least 0"),
        ("max_line_length", "wide", "max_line_length must be an integer"),
        ("ignore", ["H014"], "ignore must be a string"),
        ("extend_exclude", 5, "extend_exclude must be a string"),
    ],
    ids=[
        "profile_int",
        "indent_str",
        "indent_negative",
        "max_line_length_str",
        "ignore_list",
        "extend_exclude_int",
    ],
)
def test_set_options_invalid_type(
    djlint_plugin: DjlintPlugin,
    option_name: str,
    invalid_value: object,
    error_match: str,
) -> None:
    """Invalid option values raise ValueError.

    Args:
        djlint_plugin: The DjlintPlugin instance to test.
        option_name: The option name being tested.
        invalid_value: An invalid value for the option.
        error_match: Pattern expected in the error message.
    """
    with pytest.raises(ValueError, match=error_match):
        djlint_plugin.set_options(**{option_name: invalid_value})  # type: ignore[arg-type]


# --- Command building ---


def test_build_lint_command_basic(djlint_plugin: DjlintPlugin) -> None:
    """The check command includes the profile and --check flag.

    Args:
        djlint_plugin: The DjlintPlugin instance to test.
    """
    cmd = djlint_plugin._build_lint_command(files=["page.jinja"])

    assert_that(cmd[0]).is_equal_to("djlint")
    assert_that(cmd).contains("--check")
    assert_that(cmd).contains("--profile")
    profile_idx = cmd.index("--profile")
    assert_that(cmd[profile_idx + 1]).is_equal_to(DJLINT_DEFAULT_PROFILE)
    assert_that(cmd).contains("page.jinja")
    assert_that(cmd).does_not_contain("--reformat")


def test_build_fix_command_basic(djlint_plugin: DjlintPlugin) -> None:
    """The fix command includes the profile and --reformat flag.

    Args:
        djlint_plugin: The DjlintPlugin instance to test.
    """
    cmd = djlint_plugin._build_fix_command(files=["page.jinja"])

    assert_that(cmd[0]).is_equal_to("djlint")
    assert_that(cmd).contains("--reformat")
    assert_that(cmd).contains("--profile")
    assert_that(cmd).contains("page.jinja")
    assert_that(cmd).does_not_contain("--check")


def test_build_command_with_all_options(djlint_plugin: DjlintPlugin) -> None:
    """All option flags are mapped onto the command line.

    Args:
        djlint_plugin: The DjlintPlugin instance to test.
    """
    djlint_plugin.set_options(
        profile="django",
        indent=2,
        max_line_length=100,
        ignore="H014,H017",
        extend_exclude="vendor/",
    )
    cmd = djlint_plugin._build_lint_command(files=["page.jinja"])

    assert_that(cmd[cmd.index("--profile") + 1]).is_equal_to("django")
    assert_that(cmd[cmd.index("--indent") + 1]).is_equal_to("2")
    assert_that(cmd[cmd.index("--max-line-length") + 1]).is_equal_to("100")
    assert_that(cmd[cmd.index("--ignore") + 1]).is_equal_to("H014,H017")
    assert_that(cmd[cmd.index("--extend-exclude") + 1]).is_equal_to("vendor/")


def test_build_command_multiple_files(djlint_plugin: DjlintPlugin) -> None:
    """Multiple files are appended to the command.

    Args:
        djlint_plugin: The DjlintPlugin instance to test.
    """
    cmd = djlint_plugin._build_lint_command(files=["a.jinja", "b.twig"])

    assert_that(cmd).contains("a.jinja")
    assert_that(cmd).contains("b.twig")


# --- Definition metadata ---


def test_definition_name(djlint_plugin: DjlintPlugin) -> None:
    """The plugin exposes the djlint name.

    Args:
        djlint_plugin: The DjlintPlugin instance to test.
    """
    assert_that(djlint_plugin.definition.name).is_equal_to("djlint")


def test_definition_can_fix(djlint_plugin: DjlintPlugin) -> None:
    """The plugin advertises fix support.

    Args:
        djlint_plugin: The DjlintPlugin instance to test.
    """
    assert_that(djlint_plugin.definition.can_fix).is_true()


def test_definition_tool_type(djlint_plugin: DjlintPlugin) -> None:
    """The plugin is both a linter and a formatter.

    Args:
        djlint_plugin: The DjlintPlugin instance to test.
    """
    expected = ToolType.LINTER | ToolType.FORMATTER
    assert_that(djlint_plugin.definition.tool_type).is_equal_to(expected)


@pytest.mark.parametrize(
    "pattern",
    ["*.jinja", "*.jinja2", "*.j2", "*.twig", "*.nj"],
)
def test_definition_file_patterns(
    djlint_plugin: DjlintPlugin,
    pattern: str,
) -> None:
    """The plugin declares the expected template file patterns.

    Args:
        djlint_plugin: The DjlintPlugin instance to test.
        pattern: A file pattern expected in the definition.
    """
    assert_that(djlint_plugin.definition.file_patterns).contains(pattern)


def test_definition_native_configs(djlint_plugin: DjlintPlugin) -> None:
    """The plugin declares its native config files.

    Args:
        djlint_plugin: The DjlintPlugin instance to test.
    """
    assert_that(djlint_plugin.definition.native_configs).contains(
        "pyproject.toml",
        ".djlintrc",
    )


def test_doc_url_with_code(djlint_plugin: DjlintPlugin) -> None:
    """A rule code yields the djLint linter documentation URL.

    Args:
        djlint_plugin: The DjlintPlugin instance to test.
    """
    assert_that(djlint_plugin.doc_url("H013")).contains("djlint.com")


def test_doc_url_without_code(djlint_plugin: DjlintPlugin) -> None:
    """An empty code yields no documentation URL.

    Args:
        djlint_plugin: The DjlintPlugin instance to test.
    """
    assert_that(djlint_plugin.doc_url("")).is_none()
