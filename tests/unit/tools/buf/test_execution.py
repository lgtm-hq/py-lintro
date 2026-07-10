"""Tests for BufPlugin check and fix method execution."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import patch

from assertpy import assert_that

from lintro.parsers.buf.buf_issue import BufIssue
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.definitions.buf import BufPlugin

_LINT_ISSUE = (
    '{"path":"a.proto","start_line":2,"start_column":1,"end_line":2,'
    '"end_column":11,"type":"PACKAGE_LOWER_SNAKE_CASE","message":"m"}'
)
_FORMAT_DIFF = "--- a.proto.orig\t2026-07-07\n+++ a.proto\t2026-07-07\n@@ -1 +1 @@\n"


def _result(returncode: int, stdout: str = "") -> SubprocessResult:
    """Build a SubprocessResult with the given exit code and stdout.

    Args:
        returncode: The simulated process exit code.
        stdout: The simulated standard output.

    Returns:
        A SubprocessResult suitable for mocking ``_run_subprocess_result``.
    """
    return SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr="",
        output=stdout,
    )


def test_check_success_when_clean(buf_plugin: BufPlugin, tmp_path: Path) -> None:
    """Check succeeds when lint and format both report nothing.

    Args:
        buf_plugin: The plugin under test.
        tmp_path: Temporary directory for the proto file.
    """
    proto = tmp_path / "a.proto"
    proto.write_text('syntax = "proto3";\n\npackage a.v1;\n')

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            buf_plugin,
            "_run_subprocess_result",
            side_effect=[_result(0, ""), _result(0, "")],
        ):
            result = buf_plugin.check([str(proto)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_reports_lint_issues(buf_plugin: BufPlugin, tmp_path: Path) -> None:
    """Check surfaces lint violations from buf's JSON output.

    Args:
        buf_plugin: The plugin under test.
        tmp_path: Temporary directory for the proto file.
    """
    proto = tmp_path / "a.proto"
    proto.write_text('syntax = "proto3";\npackage a;\n')

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            buf_plugin,
            "_run_subprocess_result",
            side_effect=[_result(100, _LINT_ISSUE), _result(0, "")],
        ):
            result = buf_plugin.check([str(proto)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.issues).is_not_none()
    issue = cast(BufIssue, result.issues[0])  # type: ignore[index]
    assert_that(issue.code).is_equal_to("PACKAGE_LOWER_SNAKE_CASE")


def test_check_reports_format_issues(buf_plugin: BufPlugin, tmp_path: Path) -> None:
    """Check surfaces formatting problems from buf's diff output.

    Args:
        buf_plugin: The plugin under test.
        tmp_path: Temporary directory for the proto file.
    """
    proto = tmp_path / "a.proto"
    proto.write_text('syntax="proto3";\n')

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            buf_plugin,
            "_run_subprocess_result",
            side_effect=[_result(0, ""), _result(100, _FORMAT_DIFF)],
        ):
            result = buf_plugin.check([str(proto)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.issues).is_not_none()
    issue = cast(BufIssue, result.issues[0])  # type: ignore[index]
    assert_that(issue.code).is_equal_to("FORMAT")


def test_check_no_proto_files(buf_plugin: BufPlugin, tmp_path: Path) -> None:
    """Check returns success when no proto files match.

    Args:
        buf_plugin: The plugin under test.
        tmp_path: Temporary directory for the non-proto file.
    """
    other = tmp_path / "notes.txt"
    other.write_text("not a proto")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        result = buf_plugin.check([str(other)], {})

    assert_that(result.success).is_true()


def test_fix_formats_and_reports_remaining(
    buf_plugin: BufPlugin,
    tmp_path: Path,
) -> None:
    """Fix resolves formatting while leaving unfixable lint issues.

    Args:
        buf_plugin: The plugin under test.
        tmp_path: Temporary directory for the proto file.
    """
    proto = tmp_path / "a.proto"
    proto.write_text('syntax="proto3";\npackage a;\n')

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            buf_plugin,
            "_run_subprocess_result",
            side_effect=[
                _result(100, _FORMAT_DIFF),  # initial format check
                _result(100, _LINT_ISSUE),  # initial lint check
                _result(0, ""),  # write
                _result(0, ""),  # final format check (fixed)
                _result(100, _LINT_ISSUE),  # final lint check (still failing)
            ],
        ):
            result = buf_plugin.fix([str(proto)], {})

    assert_that(result.success).is_false()
    assert_that(result.initial_issues_count).is_equal_to(2)
    assert_that(result.fixed_issues_count).is_equal_to(1)
    assert_that(result.remaining_issues_count).is_equal_to(1)


def test_fix_all_clean(buf_plugin: BufPlugin, tmp_path: Path) -> None:
    """Fix reports success when nothing needed fixing.

    Args:
        buf_plugin: The plugin under test.
        tmp_path: Temporary directory for the proto file.
    """
    proto = tmp_path / "a.proto"
    proto.write_text('syntax = "proto3";\n\npackage a.v1;\n')

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            buf_plugin,
            "_run_subprocess_result",
            side_effect=[
                _result(0, ""),  # initial format check
                _result(0, ""),  # initial lint check
                _result(0, ""),  # write
                _result(0, ""),  # final format check
                _result(0, ""),  # final lint check
            ],
        ):
            result = buf_plugin.fix([str(proto)], {})

    assert_that(result.success).is_true()
    assert_that(result.initial_issues_count).is_equal_to(0)
    assert_that(result.fixed_issues_count).is_equal_to(0)


def test_fix_no_proto_files(buf_plugin: BufPlugin, tmp_path: Path) -> None:
    """Fix returns success when no proto files match.

    Args:
        buf_plugin: The plugin under test.
        tmp_path: Temporary directory for the non-proto file.
    """
    other = tmp_path / "notes.txt"
    other.write_text("not a proto")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        result = buf_plugin.fix([str(other)], {})

    assert_that(result.success).is_true()
    assert_that(result.output).contains("No .proto files")


def _err_result(returncode: int, stderr: str) -> SubprocessResult:
    """Build a SubprocessResult with stderr-only failure output.

    Args:
        returncode: The simulated process exit code.
        stderr: The simulated standard error.

    Returns:
        A SubprocessResult with empty stdout.
    """
    return SubprocessResult(
        returncode=returncode,
        stdout="",
        stderr=stderr,
        output=stderr,
    )


def test_check_lint_runtime_error_is_not_clean(
    buf_plugin: BufPlugin,
    tmp_path: Path,
) -> None:
    """A buf lint failure with stderr-only output fails the check.

    Args:
        buf_plugin: The plugin under test.
        tmp_path: Temporary directory for the proto file.
    """
    proto = tmp_path / "a.proto"
    proto.write_text('syntax = "proto3";\n')

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            buf_plugin,
            "_run_subprocess_result",
            side_effect=[_err_result(1, "Failure: invalid buf.yaml")],
        ):
            result = buf_plugin.check([str(proto)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).contains("invalid buf.yaml")


def test_check_format_runtime_error_is_not_clean(
    buf_plugin: BufPlugin,
    tmp_path: Path,
) -> None:
    """A buf format failure with no diff fails the check.

    Args:
        buf_plugin: The plugin under test.
        tmp_path: Temporary directory for the proto file.
    """
    proto = tmp_path / "a.proto"
    proto.write_text('syntax = "proto3";\n')

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            buf_plugin,
            "_run_subprocess_result",
            side_effect=[
                _result(0, ""),
                _err_result(2, "Failure: unreadable module root"),
            ],
        ):
            result = buf_plugin.check([str(proto)], {})

    assert_that(result.success).is_false()
    assert_that(result.output).contains("unreadable module root")


def test_fix_write_failure_surfaces_error(
    buf_plugin: BufPlugin,
    tmp_path: Path,
) -> None:
    """A failed buf format --write reports the cause, not just counts.

    Args:
        buf_plugin: The plugin under test.
        tmp_path: Temporary directory for the proto file.
    """
    proto = tmp_path / "a.proto"
    proto.write_text('syntax = "proto3";\n')

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            buf_plugin,
            "_run_subprocess_result",
            side_effect=[
                _result(1, _FORMAT_DIFF),
                _result(1, _LINT_ISSUE),
                _err_result(1, "Failure: permission denied"),
            ],
        ):
            result = buf_plugin.fix([str(proto)], {})

    assert_that(result.success).is_false()
    assert_that(result.output).contains("permission denied")
    assert_that(result.fixed_issues_count).is_equal_to(0)
