"""Unit tests for sqlfluff plugin default options and set_options validation."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.tools.sqlfluff.definition import (
    SQLFLUFF_DEFAULT_TIMEOUT,
    SqlfluffPlugin,
)

# Tests for default option values


@pytest.mark.parametrize(
    ("option_name", "expected_value"),
    [
        ("timeout", SQLFLUFF_DEFAULT_TIMEOUT),
        ("dialect", None),
        ("exclude_rules", None),
        ("rules", None),
        ("templater", None),
    ],
    ids=[
        "timeout_equals_default",
        "dialect_is_none",
        "exclude_rules_is_none",
        "rules_is_none",
        "templater_is_none",
    ],
)
def test_default_options_values(
    sqlfluff_plugin: SqlfluffPlugin,
    option_name: str,
    expected_value: object,
) -> None:
    """Default options have correct values.

    Args:
        sqlfluff_plugin: The SqlfluffPlugin instance to test.
        option_name: The name of the option to check.
        expected_value: The expected value for the option.
    """
    assert_that(
        sqlfluff_plugin.definition.default_options[option_name],
    ).is_equal_to(expected_value)


# Tests for SqlfluffPlugin.set_options method - valid options


@pytest.mark.parametrize(
    ("option_name", "option_value"),
    [
        ("dialect", "postgres"),
        ("dialect", "mysql"),
        ("dialect", "bigquery"),
        ("dialect", "snowflake"),
        ("exclude_rules", ["L001"]),
        ("exclude_rules", ["L001", "L002"]),
        ("rules", ["L002"]),
        ("rules", ["L002", "L003"]),
        ("templater", "jinja"),
        ("templater", "raw"),
        ("templater", "python"),
    ],
    ids=[
        "dialect_postgres",
        "dialect_mysql",
        "dialect_bigquery",
        "dialect_snowflake",
        "exclude_rules_single",
        "exclude_rules_multiple",
        "rules_single",
        "rules_multiple",
        "templater_jinja",
        "templater_raw",
        "templater_python",
    ],
)
def test_set_options_valid(
    sqlfluff_plugin: SqlfluffPlugin,
    option_name: str,
    option_value: object,
) -> None:
    """Set valid options correctly.

    Args:
        sqlfluff_plugin: The SqlfluffPlugin instance to test.
        option_name: The name of the option to set.
        option_value: The value to set for the option.
    """
    sqlfluff_plugin.set_options(**{option_name: option_value})  # type: ignore[arg-type]
    assert_that(sqlfluff_plugin.options.get(option_name)).is_equal_to(option_value)


# Tests for SqlfluffPlugin.set_options method - invalid types


@pytest.mark.parametrize(
    ("option_name", "invalid_value", "error_match"),
    [
        ("dialect", 123, "dialect must be a string"),
        ("dialect", ["postgres"], "dialect must be a string"),
        ("exclude_rules", "L001", "exclude_rules must be a list"),
        ("exclude_rules", 123, "exclude_rules must be a list"),
        ("rules", "L002", "rules must be a list"),
        ("rules", 123, "rules must be a list"),
        ("templater", 123, "templater must be a string"),
        ("templater", ["jinja"], "templater must be a string"),
    ],
    ids=[
        "invalid_dialect_int",
        "invalid_dialect_list",
        "invalid_exclude_rules_str",
        "invalid_exclude_rules_int",
        "invalid_rules_str",
        "invalid_rules_int",
        "invalid_templater_int",
        "invalid_templater_list",
    ],
)
def test_set_options_invalid_type(
    sqlfluff_plugin: SqlfluffPlugin,
    option_name: str,
    invalid_value: object,
    error_match: str,
) -> None:
    """Raise ValueError for invalid option types.

    Args:
        sqlfluff_plugin: The SqlfluffPlugin instance to test.
        option_name: The name of the option being tested.
        invalid_value: An invalid value for the option.
        error_match: Pattern expected in the error message.
    """
    with pytest.raises(ValueError, match=error_match):
        sqlfluff_plugin.set_options(**{option_name: invalid_value})  # type: ignore[arg-type]


# Tests for SqlfluffPlugin._build_lint_command method


def test_build_lint_command_basic(sqlfluff_plugin: SqlfluffPlugin) -> None:
    """Build basic lint command without extra options.

    Args:
        sqlfluff_plugin: The SqlfluffPlugin instance to test.
    """
    from lintro.tools.sqlfluff.definition import SQLFLUFF_DEFAULT_FORMAT

    cmd = sqlfluff_plugin._build_lint_command(files=["test.sql"])

    assert_that(cmd).contains("sqlfluff")
    assert_that(cmd).contains("lint")
    assert_that(cmd).contains("--format")
    assert_that(cmd).contains(SQLFLUFF_DEFAULT_FORMAT)
    assert_that(cmd).contains("test.sql")


def test_build_lint_command_with_dialect(sqlfluff_plugin: SqlfluffPlugin) -> None:
    """Build lint command with dialect option.

    Args:
        sqlfluff_plugin: The SqlfluffPlugin instance to test.
    """
    sqlfluff_plugin.set_options(dialect="postgres")
    cmd = sqlfluff_plugin._build_lint_command(files=["test.sql"])

    assert_that(cmd).contains("--dialect")
    dialect_idx = cmd.index("--dialect")
    assert_that(cmd[dialect_idx + 1]).is_equal_to("postgres")


def test_build_lint_command_with_exclude_rules(
    sqlfluff_plugin: SqlfluffPlugin,
) -> None:
    """Build lint command with exclude_rules option.

    Args:
        sqlfluff_plugin: The SqlfluffPlugin instance to test.
    """
    sqlfluff_plugin.set_options(exclude_rules=["L001", "L002"])
    cmd = sqlfluff_plugin._build_lint_command(files=["test.sql"])

    assert_that(cmd).contains("--exclude-rules")
    # Rules should be comma-separated per SQLFluff CLI docs
    exclude_idx = cmd.index("--exclude-rules")
    assert_that(cmd[exclude_idx + 1]).is_equal_to("L001,L002")


def test_build_lint_command_with_rules(sqlfluff_plugin: SqlfluffPlugin) -> None:
    """Build lint command with rules option.

    Args:
        sqlfluff_plugin: The SqlfluffPlugin instance to test.
    """
    sqlfluff_plugin.set_options(rules=["L002", "L003"])
    cmd = sqlfluff_plugin._build_lint_command(files=["test.sql"])

    assert_that(cmd).contains("--rules")
    # Rules should be comma-separated per SQLFluff CLI docs
    rules_idx = cmd.index("--rules")
    assert_that(cmd[rules_idx + 1]).is_equal_to("L002,L003")


def test_build_lint_command_with_templater(sqlfluff_plugin: SqlfluffPlugin) -> None:
    """Build lint command with templater option.

    Args:
        sqlfluff_plugin: The SqlfluffPlugin instance to test.
    """
    sqlfluff_plugin.set_options(templater="jinja")
    cmd = sqlfluff_plugin._build_lint_command(files=["test.sql"])

    assert_that(cmd).contains("--templater")
    templater_idx = cmd.index("--templater")
    assert_that(cmd[templater_idx + 1]).is_equal_to("jinja")


def test_build_lint_command_with_all_options(sqlfluff_plugin: SqlfluffPlugin) -> None:
    """Build lint command with all options set.

    Args:
        sqlfluff_plugin: The SqlfluffPlugin instance to test.
    """
    sqlfluff_plugin.set_options(
        dialect="postgres",
        exclude_rules=["L001"],
        rules=["L002"],
        templater="jinja",
    )
    cmd = sqlfluff_plugin._build_lint_command(files=["test.sql"])

    assert_that(cmd).contains("--dialect")
    assert_that(cmd).contains("--exclude-rules")
    assert_that(cmd).contains("--rules")
    assert_that(cmd).contains("--templater")
    assert_that(cmd).contains("test.sql")


def test_build_lint_command_multiple_files(sqlfluff_plugin: SqlfluffPlugin) -> None:
    """Build lint command with multiple files.

    Args:
        sqlfluff_plugin: The SqlfluffPlugin instance to test.
    """
    cmd = sqlfluff_plugin._build_lint_command(files=["test1.sql", "test2.sql"])

    assert_that(cmd).contains("test1.sql")
    assert_that(cmd).contains("test2.sql")


# Tests for SqlfluffPlugin._build_fix_command method


def test_build_fix_command_basic(sqlfluff_plugin: SqlfluffPlugin) -> None:
    """Build basic fix command without extra options.

    Args:
        sqlfluff_plugin: The SqlfluffPlugin instance to test.
    """
    cmd = sqlfluff_plugin._build_fix_command(files=["test.sql"])

    assert_that(cmd).contains("sqlfluff")
    assert_that(cmd).contains("fix")
    assert_that(cmd).contains("--force")
    assert_that(cmd).contains("test.sql")


def test_build_fix_command_with_dialect(sqlfluff_plugin: SqlfluffPlugin) -> None:
    """Build fix command with dialect option.

    Args:
        sqlfluff_plugin: The SqlfluffPlugin instance to test.
    """
    sqlfluff_plugin.set_options(dialect="mysql")
    cmd = sqlfluff_plugin._build_fix_command(files=["test.sql"])

    assert_that(cmd).contains("--dialect")
    dialect_idx = cmd.index("--dialect")
    assert_that(cmd[dialect_idx + 1]).is_equal_to("mysql")


def test_build_fix_command_with_exclude_rules(
    sqlfluff_plugin: SqlfluffPlugin,
) -> None:
    """Build fix command with exclude_rules option.

    Args:
        sqlfluff_plugin: The SqlfluffPlugin instance to test.
    """
    sqlfluff_plugin.set_options(exclude_rules=["L001"])
    cmd = sqlfluff_plugin._build_fix_command(files=["test.sql"])

    assert_that(cmd).contains("--exclude-rules")


def test_build_fix_command_with_rules(sqlfluff_plugin: SqlfluffPlugin) -> None:
    """Build fix command with rules option.

    Args:
        sqlfluff_plugin: The SqlfluffPlugin instance to test.
    """
    sqlfluff_plugin.set_options(rules=["L002"])
    cmd = sqlfluff_plugin._build_fix_command(files=["test.sql"])

    assert_that(cmd).contains("--rules")


def test_build_fix_command_with_templater(sqlfluff_plugin: SqlfluffPlugin) -> None:
    """Build fix command with templater option.

    Args:
        sqlfluff_plugin: The SqlfluffPlugin instance to test.
    """
    sqlfluff_plugin.set_options(templater="raw")
    cmd = sqlfluff_plugin._build_fix_command(files=["test.sql"])

    assert_that(cmd).contains("--templater")
    templater_idx = cmd.index("--templater")
    assert_that(cmd[templater_idx + 1]).is_equal_to("raw")


def test_build_fix_command_with_all_options(sqlfluff_plugin: SqlfluffPlugin) -> None:
    """Build fix command with all options set.

    Args:
        sqlfluff_plugin: The SqlfluffPlugin instance to test.
    """
    sqlfluff_plugin.set_options(
        dialect="bigquery",
        exclude_rules=["L001"],
        rules=["L002"],
        templater="python",
    )
    cmd = sqlfluff_plugin._build_fix_command(files=["test.sql"])

    assert_that(cmd).contains("--dialect")
    assert_that(cmd).contains("--exclude-rules")
    assert_that(cmd).contains("--rules")
    assert_that(cmd).contains("--templater")
    assert_that(cmd).contains("--force")
    assert_that(cmd).contains("test.sql")


# Tests for plugin definition


def test_plugin_definition_name(sqlfluff_plugin: SqlfluffPlugin) -> None:
    """Plugin definition has correct name.

    Args:
        sqlfluff_plugin: The SqlfluffPlugin instance to test.
    """
    assert_that(sqlfluff_plugin.definition.name).is_equal_to("sqlfluff")


def test_plugin_definition_can_fix(sqlfluff_plugin: SqlfluffPlugin) -> None:
    """Plugin definition indicates it can fix issues.

    Args:
        sqlfluff_plugin: The SqlfluffPlugin instance to test.
    """
    assert_that(sqlfluff_plugin.definition.can_fix).is_true()


def test_plugin_definition_file_patterns(sqlfluff_plugin: SqlfluffPlugin) -> None:
    """Plugin definition has correct file patterns.

    Args:
        sqlfluff_plugin: The SqlfluffPlugin instance to test.
    """
    assert_that(sqlfluff_plugin.definition.file_patterns).contains("*.sql")


def test_plugin_definition_native_configs(sqlfluff_plugin: SqlfluffPlugin) -> None:
    """Plugin definition has correct native config files.

    Args:
        sqlfluff_plugin: The SqlfluffPlugin instance to test.
    """
    assert_that(sqlfluff_plugin.definition.native_configs).contains(".sqlfluff")
    assert_that(sqlfluff_plugin.definition.native_configs).contains("pyproject.toml")
