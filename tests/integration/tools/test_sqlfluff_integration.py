"""Integration tests for SQLFluff tool definition.

These tests require sqlfluff to be installed and available in PATH.
They verify the SqlfluffPlugin definition, check command, fix command,
and set_options method.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

from tests.integration._tools import require_tool

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

pytestmark = require_tool("sqlfluff")


@pytest.fixture
def temp_sql_file_with_issues(tmp_path: Path) -> str:
    """Create a temporary SQL file with linting issues.

    Creates a file containing SQL with deliberate linting issues
    that sqlfluff should detect, including:
    - Lowercase keywords
    - Missing aliases

    Also creates a .sqlfluff config file with dialect=ansi since
    SQLFluff v3.0.0+ requires a dialect to be specified.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the created file as a string.
    """
    # Create .sqlfluff config with dialect (required in v3.0.0+)
    config_path = tmp_path / ".sqlfluff"
    config_path.write_text(
        """\
[sqlfluff]
dialect = ansi
""",
    )
    file_path = tmp_path / "query.sql"
    file_path.write_text(
        """\
-- SQL file with linting issues
select id, name from users where id = 1;
SELECT u.id, u.name FROM users u;
""",
    )
    return str(file_path)


@pytest.fixture
def temp_sql_file_clean(tmp_path: Path) -> str:
    """Create a temporary SQL file with no issues.

    Creates a file containing properly formatted SQL that should pass
    sqlfluff checking with minimal issues.

    Also creates a .sqlfluff config file with dialect=ansi since
    SQLFluff v3.0.0+ requires a dialect to be specified.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the created file as a string.
    """
    # Create .sqlfluff config with dialect (required in v3.0.0+)
    config_path = tmp_path / ".sqlfluff"
    config_path.write_text(
        """\
[sqlfluff]
dialect = ansi
""",
    )
    file_path = tmp_path / "clean.sql"
    file_path.write_text(
        """\
-- Clean SQL file
SELECT
    id,
    name
FROM users
WHERE id = 1;
""",
    )
    return str(file_path)


# --- Tests for SqlfluffPlugin definition ---


@pytest.mark.parametrize(
    ("attr", "expected"),
    [
        ("name", "sqlfluff"),
        ("can_fix", True),
    ],
    ids=["name", "can_fix"],
)
def test_definition_attributes(
    get_plugin: Callable[[str], BaseToolPlugin],
    attr: str,
    expected: object,
) -> None:
    """Verify SqlfluffPlugin definition has correct attribute values.

    Tests that the plugin definition exposes the expected values for
    name and can_fix attributes.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        attr: The attribute name to check on the definition.
        expected: The expected value of the attribute.
    """
    sqlfluff_plugin = get_plugin("sqlfluff")
    assert_that(getattr(sqlfluff_plugin.definition, attr)).is_equal_to(expected)


def test_definition_file_patterns(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify SqlfluffPlugin definition includes expected file patterns.

    Tests that the plugin is configured to handle SQL files.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    sqlfluff_plugin = get_plugin("sqlfluff")
    assert_that(sqlfluff_plugin.definition.file_patterns).contains("*.sql")


def test_definition_tool_type(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify SqlfluffPlugin is both a linter and formatter.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    from lintro.enums.tool_type import ToolType

    sqlfluff_plugin = get_plugin("sqlfluff")
    expected_type = ToolType.LINTER | ToolType.FORMATTER
    assert_that(sqlfluff_plugin.definition.tool_type).is_equal_to(expected_type)


# --- Integration tests for sqlfluff check command ---


def test_check_file_with_issues(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_sql_file_with_issues: str,
) -> None:
    """Verify sqlfluff check detects linting issues in SQL files.

    Runs sqlfluff on a file containing deliberate linting issues
    and verifies that issues are found.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_sql_file_with_issues: Path to file with linting issues.
    """
    sqlfluff_plugin = get_plugin("sqlfluff")
    result = sqlfluff_plugin.check([temp_sql_file_with_issues], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("sqlfluff")
    # SQLFluff should detect at least one issue
    assert_that(result.issues_count).is_greater_than(0)
    # success should be False when issues are detected
    assert_that(result.success).is_false()


def test_check_clean_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_sql_file_clean: str,
) -> None:
    """Verify sqlfluff check handles clean files.

    Runs sqlfluff on a properly formatted file and verifies the result.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_sql_file_clean: Path to file with no issues.
    """
    sqlfluff_plugin = get_plugin("sqlfluff")
    result = sqlfluff_plugin.check([temp_sql_file_clean], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("sqlfluff")
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_unknown_dialect_is_an_execution_failure_not_a_malformed_report(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_sql_file_clean: str,
) -> None:
    """SQLFluff's own configuration error is named, with no parse failure.

    SQLFluff exits 2 and prints ``Error: Unknown dialect 'bogus'`` on stdout,
    so neither check nor fix may call that a malformed JSON report (#3352).

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_sql_file_clean: Path to a file SQLFluff would otherwise pass.
    """
    sqlfluff_plugin = get_plugin("sqlfluff")
    sqlfluff_plugin.set_options(dialect="bogus")

    checked = sqlfluff_plugin.check([temp_sql_file_clean], {})
    fixed = sqlfluff_plugin.fix([temp_sql_file_clean], {})

    for result in (checked, fixed):
        assert_that(result.success).is_false()
        assert_that(result.execution_failed).is_true()
        assert_that(result.findings_complete).is_false()
        assert_that(result.parse_failures_count).is_zero()
        assert_that(result.output).contains(
            "SQLFluff could not run (exit 2): Error: Unknown dialect 'bogus'",
        ).does_not_contain("Malformed")
    assert_that(fixed.residual_unknown).is_true()
    assert_that(fixed.fixed_issues_count).is_none()


def test_malformed_config_is_an_unmeasured_execution_failure(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """A config SQLFluff cannot load prints no report: nothing is measured.

    SQLFluff 4.5.0 exits 1 with an empty stdout and a
    ``MissingSectionHeaderError`` traceback on stderr when ``.sqlfluff`` has
    a broken section header (#3352).

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Directory holding the broken config and a SQL file.
    """
    (tmp_path / ".sqlfluff").write_text("[sqlfluff\ndialect = ansi\n")
    sql = tmp_path / "query.sql"
    sql.write_text("select a from b\n")
    sqlfluff_plugin = get_plugin("sqlfluff")

    checked = sqlfluff_plugin.check([str(sql)], {})
    fixed = sqlfluff_plugin.fix([str(sql)], {})

    for result in (checked, fixed):
        assert_that(result.success).is_false()
        assert_that(result.execution_failed).is_true()
        assert_that(result.parse_failures_count).is_zero()
        assert_that(result.output).contains(
            "SQLFluff printed no report (exit 1): configparser."
            "MissingSectionHeaderError",
        ).does_not_contain("Traceback")
    assert_that(fixed.residual_unknown).is_true()
    assert_that(fixed.fixed_issues_count).is_none()
    assert_that(fixed.remaining_issues_count).is_none()


def test_check_empty_directory(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify sqlfluff check handles empty directories gracefully.

    Runs sqlfluff on an empty directory and verifies a result is returned
    without errors.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    sqlfluff_plugin = get_plugin("sqlfluff")
    result = sqlfluff_plugin.check([str(tmp_path)], {})

    assert_that(result).is_not_none()


# --- Integration tests for sqlfluff fix command ---


def test_fix_formats_sql_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_sql_file_with_issues: str,
) -> None:
    """Verify sqlfluff fix formats SQL files.

    Runs sqlfluff fix on a file with linting issues and verifies
    that the operation completes successfully.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_sql_file_with_issues: Path to file with linting issues.
    """
    sqlfluff_plugin = get_plugin("sqlfluff")
    result = sqlfluff_plugin.fix([temp_sql_file_with_issues], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("sqlfluff")
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


# --- Tests for SqlfluffPlugin.set_options method ---


@pytest.mark.parametrize(
    ("option_name", "option_value"),
    [
        ("dialect", "ansi"),
        ("dialect", "postgres"),
        ("dialect", "mysql"),
        ("templater", "raw"),
        ("templater", "jinja"),
        ("exclude_rules", ["L010", "L014"]),
        ("rules", ["L001", "L002"]),
    ],
    ids=[
        "dialect_ansi",
        "dialect_postgres",
        "dialect_mysql",
        "templater_raw",
        "templater_jinja",
        "exclude_rules",
        "rules",
    ],
)
def test_set_options(
    get_plugin: Callable[[str], BaseToolPlugin],
    option_name: str,
    option_value: object,
) -> None:
    """Verify SqlfluffPlugin.set_options correctly sets various options.

    Tests that plugin options can be set and retrieved correctly.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        option_name: Name of the option to set.
        option_value: Value to set for the option.
    """
    sqlfluff_plugin = get_plugin("sqlfluff")
    sqlfluff_plugin.set_options(**{option_name: option_value})
    assert_that(sqlfluff_plugin.options.get(option_name)).is_equal_to(option_value)


def test_files_sqlfluffignore_excludes_are_not_claimed(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """A file ``.sqlfluffignore`` excludes is never reported as inspected.

    SQLFluff prints ``[]`` at exit 0 for an ignored file, while a linted one
    always gets a record (#3388). Check and fix over only ignored files are
    no-files runs; adding a linted file makes an ordinary run.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Project holding the config, the ignore file and SQL files.
    """
    (tmp_path / ".sqlfluff").write_text("[sqlfluff]\ndialect = ansi\n")
    (tmp_path / ".sqlfluffignore").write_text("ignored.sql\n")
    ignored = tmp_path / "ignored.sql"
    ignored.write_text("select * from t\n")
    linted = tmp_path / "linted.sql"
    linted.write_text("SELECT a FROM t\n")
    sqlfluff_plugin = get_plugin("sqlfluff")

    checked = sqlfluff_plugin.check([str(ignored)], {})
    fixed = sqlfluff_plugin.fix([str(ignored)], {})
    mixed = sqlfluff_plugin.check([str(ignored), str(linted)], {})

    for result in (checked, fixed):
        assert_that(result.success).is_true()
        assert_that(result.no_files).is_true()
        assert_that(result.inspected_files).is_false()
        assert_that(result.output).contains(".sqlfluffignore")
    assert_that(ignored.read_text()).is_equal_to("select * from t\n")
    assert_that(mixed.no_files).is_false()
    assert_that(mixed.inspected_files).is_true()
