"""Unit tests for sqlfluff plugin error handling, timeouts, and edge cases."""

from __future__ import annotations

import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.tools.definitions.sqlfluff import (
    SQLFLUFF_DEFAULT_TIMEOUT,
    SqlfluffPlugin,
)
from tests.test_samples_helpers import copy_sample

# Tests for check method error handling


def test_check_with_timeout(
    sqlfluff_plugin: SqlfluffPlugin,
    tmp_path: Path,
) -> None:
    """Check handles timeout correctly.

    Args:
        sqlfluff_plugin: The SqlfluffPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test_query.sql"
    copy_sample(
        tmp_path,
        "tools",
        "sql",
        "sqlfluff",
        "sqlfluff_violations.sql",
        dest_name=test_file.name,
    )

    # Note: verify_tool_version is already patched by the sqlfluff_plugin fixture
    with patch.object(
        sqlfluff_plugin,
        "_run_subprocess",
        side_effect=subprocess.TimeoutExpired(
            cmd=["sqlfluff"],
            timeout=SQLFLUFF_DEFAULT_TIMEOUT,
        ),
    ):
        result = sqlfluff_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()


def test_check_with_empty_output(
    sqlfluff_plugin: SqlfluffPlugin,
    tmp_path: Path,
) -> None:
    """Check handles empty output correctly.

    Args:
        sqlfluff_plugin: The SqlfluffPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test_query.sql"
    copy_sample(
        tmp_path,
        "tools",
        "sql",
        "sqlfluff",
        "sqlfluff_violations.sql",
        dest_name=test_file.name,
    )

    # Note: verify_tool_version is already patched by the sqlfluff_plugin fixture
    with patch.object(
        sqlfluff_plugin,
        "_run_subprocess",
        return_value=(True, ""),
    ):
        result = sqlfluff_plugin.check([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


# Tests for fix method error handling


def test_fix_with_timeout(
    sqlfluff_plugin: SqlfluffPlugin,
    tmp_path: Path,
) -> None:
    """Fix handles timeout correctly.

    Args:
        sqlfluff_plugin: The SqlfluffPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test_query.sql"
    copy_sample(
        tmp_path,
        "tools",
        "sql",
        "sqlfluff",
        "sqlfluff_violations.sql",
        dest_name=test_file.name,
    )

    # Note: verify_tool_version is already patched by the sqlfluff_plugin fixture
    with patch.object(
        sqlfluff_plugin,
        "_run_subprocess",
        side_effect=subprocess.TimeoutExpired(
            cmd=["sqlfluff"],
            timeout=SQLFLUFF_DEFAULT_TIMEOUT,
        ),
    ):
        result = sqlfluff_plugin.fix([str(test_file)], {})

    assert_that(result.success).is_false()
