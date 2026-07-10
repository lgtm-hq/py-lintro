"""Unit tests for the DotenvLinterPlugin.

These tests exercise the plugin's option handling, command construction, and
check/fix flows with the dotenv-linter subprocess mocked, so they run without
the binary installed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.enums.tool_type import ToolType
from lintro.parsers.dotenv_linter.dotenv_linter_issue import DotenvLinterIssue
from lintro.tools.definitions.dotenv_linter import (
    DOTENV_LINTER_DEFAULT_TIMEOUT,
    DotenvLinterPlugin,
)

# Real ``dotenv-linter check --plain`` output for a file with issues.
CHECK_OUTPUT_WITH_ISSUES = (
    "Checking .env\n"
    ".env:2 LowercaseKey: The foo key should be in uppercase\n"
    ".env:3 KeyWithoutValue: The BAR key should be with a value\n"
    "\n"
    "Found 2 problems\n"
)


def test_definition_metadata(dotenv_linter_plugin: DotenvLinterPlugin) -> None:
    """The tool definition exposes the expected core metadata."""
    definition = dotenv_linter_plugin.definition
    assert_that(definition.name).is_equal_to("dotenv_linter")
    assert_that(definition.can_fix).is_true()
    assert_that(definition.tool_type).is_equal_to(ToolType.LINTER)
    assert_that(definition.file_patterns).contains(".env")
    assert_that(definition.version_command).is_equal_to(
        ["dotenv-linter", "--version"],
    )


def test_default_timeout_option(dotenv_linter_plugin: DotenvLinterPlugin) -> None:
    """The default timeout option matches the module constant."""
    assert_that(dotenv_linter_plugin.options.get("timeout")).is_equal_to(
        DOTENV_LINTER_DEFAULT_TIMEOUT,
    )


def test_doc_url_converts_check_name_to_snake_case(
    dotenv_linter_plugin: DotenvLinterPlugin,
) -> None:
    """doc_url builds a deep link using the snake_case check name."""
    url = dotenv_linter_plugin.doc_url("LowercaseKey")
    assert_that(url).is_equal_to(
        "https://dotenv-linter.github.io/#/checks/lowercase_key",
    )


def test_doc_url_returns_none_for_empty_code(
    dotenv_linter_plugin: DotenvLinterPlugin,
) -> None:
    """doc_url returns None when no check name is provided."""
    assert_that(dotenv_linter_plugin.doc_url("")).is_none()


def test_set_options_builds_command_flags(
    dotenv_linter_plugin: DotenvLinterPlugin,
) -> None:
    """set_options wires recursive, exclude, skip_checks, and schema flags."""
    dotenv_linter_plugin.set_options(
        recursive=True,
        exclude="vendor",
        skip_checks=["LowercaseKey", "UnorderedKey"],
        schema="schema.json",
    )
    args = dotenv_linter_plugin._build_common_args()

    assert_that(args).contains("--plain")
    assert_that(args).contains("--recursive")
    assert_that(args).contains("--exclude", "vendor")
    assert_that(args).contains("--ignore-checks", "LowercaseKey", "UnorderedKey")
    assert_that(args).contains("--schema", "schema.json")


def test_check_success_on_clean_file(
    dotenv_linter_plugin: DotenvLinterPlugin,
    tmp_path: Path,
) -> None:
    """Check reports success when dotenv-linter finds no problems."""
    env_file = tmp_path / ".env"
    env_file.write_text("ABC=1\n")

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            dotenv_linter_plugin,
            "_run_subprocess",
            return_value=(True, "Checking .env\n\nNo problems found\n"),
        ),
    ):
        result = dotenv_linter_plugin.check([str(env_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_reports_issues(
    dotenv_linter_plugin: DotenvLinterPlugin,
    tmp_path: Path,
) -> None:
    """Check surfaces parsed issues when dotenv-linter finds problems."""
    env_file = tmp_path / ".env"
    env_file.write_text("foo=bar\n")

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            dotenv_linter_plugin,
            "_run_subprocess",
            return_value=(False, CHECK_OUTPUT_WITH_ISSUES),
        ),
    ):
        result = dotenv_linter_plugin.check([str(env_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(2)


def test_check_timeout_returns_failure(
    dotenv_linter_plugin: DotenvLinterPlugin,
    tmp_path: Path,
) -> None:
    """Check handles a subprocess timeout without raising."""
    env_file = tmp_path / ".env"
    env_file.write_text("ABC=1\n")

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            dotenv_linter_plugin,
            "_run_subprocess",
            side_effect=subprocess.TimeoutExpired(cmd=["dotenv-linter"], timeout=30),
        ),
    ):
        result = dotenv_linter_plugin.check([str(env_file)], {})

    assert_that(result.success).is_false()


def test_check_nonzero_exit_without_issues_is_failure(
    dotenv_linter_plugin: DotenvLinterPlugin,
    tmp_path: Path,
) -> None:
    """A non-zero exit with no parsed issues is treated as a real failure."""
    env_file = tmp_path / ".env"
    env_file.write_text("ABC=1\n")

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            dotenv_linter_plugin,
            "_run_subprocess",
            return_value=(False, "error: some invocation failure"),
        ),
    ):
        result = dotenv_linter_plugin.check([str(env_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)


def test_fix_preserves_issue_count_invariant(
    dotenv_linter_plugin: DotenvLinterPlugin,
    tmp_path: Path,
) -> None:
    """Fix satisfies initial == fixed + remaining after auto-fixing."""
    env_file = tmp_path / ".env"
    env_file.write_text("foo=bar\n")

    # First check finds 2 issues; fix succeeds; re-check finds none.
    subprocess_returns = [
        (False, CHECK_OUTPUT_WITH_ISSUES),  # initial check
        (True, "Fixing .env\nAll warnings are fixed. Total: 2\n"),  # fix
        (True, "Checking .env\n\nNo problems found\n"),  # re-check
    ]

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            dotenv_linter_plugin,
            "_run_subprocess",
            side_effect=subprocess_returns,
        ),
    ):
        result = dotenv_linter_plugin.fix([str(env_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.initial_issues_count).is_equal_to(2)
    assert_that(result.fixed_issues_count).is_equal_to(2)
    assert_that(result.remaining_issues_count).is_equal_to(0)
    assert_that(
        (result.fixed_issues_count or 0) + (result.remaining_issues_count or 0),
    ).is_equal_to(result.initial_issues_count or 0)


def test_fix_reports_remaining_when_not_all_fixed(
    dotenv_linter_plugin: DotenvLinterPlugin,
    tmp_path: Path,
) -> None:
    """Fix counts issues that remain after the fix pass."""
    env_file = tmp_path / ".env"
    env_file.write_text("foo=bar\n")

    remaining_output = (
        "Checking .env\n"
        ".env:2 LowercaseKey: The foo key should be in uppercase\n"
        "\n"
        "Found 1 problems\n"
    )
    subprocess_returns = [
        (False, CHECK_OUTPUT_WITH_ISSUES),  # initial check: 2 issues
        (True, "Fixing .env\n"),  # fix
        (False, remaining_output),  # re-check: 1 remains
    ]

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            dotenv_linter_plugin,
            "_run_subprocess",
            side_effect=subprocess_returns,
        ),
    ):
        result = dotenv_linter_plugin.fix([str(env_file)], {})

    assert_that(result.initial_issues_count).is_equal_to(2)
    assert_that(result.fixed_issues_count).is_equal_to(1)
    assert_that(result.remaining_issues_count).is_equal_to(1)
    assert_that(result.success).is_false()


def test_fix_marks_surviving_issues_not_fixable(
    dotenv_linter_plugin: DotenvLinterPlugin,
    tmp_path: Path,
) -> None:
    """Issues that survive an attempted fix are reported as non-fixable."""
    env_file = tmp_path / ".env"
    env_file.write_text("foo=bar\n")

    remaining_output = (
        "Checking .env\n"
        ".env:2 LowercaseKey: The foo key should be in uppercase\n"
        "\n"
        "Found 1 problems\n"
    )
    subprocess_returns = [
        (False, CHECK_OUTPUT_WITH_ISSUES),
        (True, "Fixing .env\n"),
        (False, remaining_output),
    ]

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            dotenv_linter_plugin,
            "_run_subprocess",
            side_effect=subprocess_returns,
        ),
    ):
        result = dotenv_linter_plugin.fix([str(env_file)], {})

    assert_that(result.issues).is_not_empty()
    remaining = [
        issue for issue in (result.issues or []) if isinstance(issue, DotenvLinterIssue)
    ]
    assert_that([issue.fixable for issue in remaining]).contains_only(False)
    # issues_count reflects what the re-check reported, matching ``issues``.
    assert_that(result.issues_count).is_equal_to(len(remaining))
