"""Unit tests for SwiftlintPlugin check execution and metadata."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.enums.tool_type import ToolType
from lintro.tools.definitions.swiftlint import SwiftlintPlugin
from tests.unit.tools.swiftlint.conftest import SAMPLE_JSON


def _sp(success: bool, output: str):
    """Build a SubprocessResult from a legacy (success, output) pair.

    Args:
        success: Whether the simulated process exited 0.
        output: Simulated stdout (also used as combined output).

    Returns:
        SubprocessResult with the JSON payload on stdout.
    """
    from lintro.plugins.subprocess_executor import SubprocessResult

    return SubprocessResult(
        returncode=0 if success else 1,
        stdout=output,
        stderr="",
        output=output,
    )


def test_definition_metadata(swiftlint_plugin: SwiftlintPlugin) -> None:
    """The tool definition advertises the expected capabilities.

    Args:
        swiftlint_plugin: The SwiftlintPlugin instance under test.
    """
    definition = swiftlint_plugin.definition
    assert_that(definition.name).is_equal_to("swiftlint")
    assert_that(definition.can_fix).is_true()
    assert_that(definition.tool_type & ToolType.LINTER).is_true()
    assert_that(definition.file_patterns).contains("*.swift")
    assert_that(definition.native_configs).contains(".swiftlint.yml")
    assert_that(definition.version_command).is_equal_to(["swiftlint", "version"])


def test_doc_url_uses_rule_slug(swiftlint_plugin: SwiftlintPlugin) -> None:
    """doc_url builds a realm.github.io URL from the rule id.

    Args:
        swiftlint_plugin: The SwiftlintPlugin instance under test.
    """
    url = swiftlint_plugin.doc_url("identifier_name")
    assert_that(url).is_equal_to(
        "https://realm.github.io/SwiftLint/identifier_name.html",
    )


def test_doc_url_empty_code_returns_none(swiftlint_plugin: SwiftlintPlugin) -> None:
    """doc_url returns None for an empty code.

    Args:
        swiftlint_plugin: The SwiftlintPlugin instance under test.
    """
    assert_that(swiftlint_plugin.doc_url("")).is_none()


def test_check_clean_file_success(
    swiftlint_plugin: SwiftlintPlugin,
    tmp_path: Path,
) -> None:
    """Check returns success when SwiftLint reports no issues.

    Args:
        swiftlint_plugin: The SwiftlintPlugin instance under test.
        tmp_path: Temporary directory for test files.
    """
    test_file = tmp_path / "Clean.swift"
    test_file.write_text('import Foundation\n\nlet greeting = "hi"\nprint(greeting)\n')

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            swiftlint_plugin,
            "_run_subprocess_result",
            return_value=_sp(True, "[\n\n]"),
        ):
            result = swiftlint_plugin.check([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_reports_issues(
    swiftlint_plugin: SwiftlintPlugin,
    tmp_path: Path,
) -> None:
    """Check surfaces parsed issues and fails when violations exist.

    SwiftLint exits non-zero (here modeled as ``success=False``) while still
    emitting JSON diagnostics, which the plugin must still parse.

    Args:
        swiftlint_plugin: The SwiftlintPlugin instance under test.
        tmp_path: Temporary directory for test files.
    """
    test_file = tmp_path / "Sample.swift"
    test_file.write_text("class foo {}\n")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            swiftlint_plugin,
            "_run_subprocess_result",
            return_value=_sp(False, SAMPLE_JSON),
        ):
            result = swiftlint_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(2)
    codes = {issue.code for issue in result.issues}
    assert_that(codes).contains("identifier_name", "type_name")


def test_check_no_swift_files(
    swiftlint_plugin: SwiftlintPlugin,
    tmp_path: Path,
) -> None:
    """Check returns success when there are no Swift files to inspect.

    Args:
        swiftlint_plugin: The SwiftlintPlugin instance under test.
        tmp_path: Temporary directory for test files.
    """
    other = tmp_path / "notes.txt"
    other.write_text("not swift")

    with patch.object(swiftlint_plugin, "_verify_tool_version", return_value=None):
        result = swiftlint_plugin.check([str(other)], {})

    assert_that(result.success).is_true()
    assert_that(result.output).contains("No")


def test_check_nonzero_exit_without_issues_fails(
    swiftlint_plugin: SwiftlintPlugin,
    tmp_path: Path,
) -> None:
    """A non-zero exit with no parsed issues is reported as a failure.

    Args:
        swiftlint_plugin: The SwiftlintPlugin instance under test.
        tmp_path: Temporary directory for test files.
    """
    test_file = tmp_path / "Broken.swift"
    test_file.write_text("class {\n")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            swiftlint_plugin,
            "_run_subprocess_result",
            return_value=_sp(False, "error: unable to parse"),
        ):
            result = swiftlint_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
