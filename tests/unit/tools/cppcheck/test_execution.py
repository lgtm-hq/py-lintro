"""Unit tests for cppcheck plugin execution."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.enums.tool_name import ToolName
from lintro.parsers.cppcheck.cppcheck_issue import CppcheckIssue
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.definitions.cppcheck import CppcheckPlugin

ISSUE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<results version="2">
    <errors>
        <error id="uninitvar" severity="error" msg="Uninitialized variable: value" cwe="457" file0="a.c">
            <location file="a.c" line="11" column="12"/>
            <symbol>value</symbol>
        </error>
    </errors>
</results>"""

CLEAN_XML = """<?xml version="1.0" encoding="UTF-8"?>
<results version="2">
    <errors>
    </errors>
</results>"""


def _ctx(tmp_path: Path, file_path: Path) -> MagicMock:
    """Build a mocked execution context.

    Args:
        tmp_path: Temporary directory path.
        file_path: Source file included in the context.

    Returns:
        A MagicMock standing in for the ExecutionContext.
    """
    ctx = MagicMock()
    ctx.should_skip = False
    ctx.early_result = None
    ctx.timeout = 60
    ctx.cwd = str(tmp_path)
    ctx.files = [str(file_path)]
    return ctx


def _subprocess_result(returncode: int, stderr: str) -> SubprocessResult:
    """Build a SubprocessResult with the XML report on stderr.

    Args:
        returncode: Simulated exit code.
        stderr: Simulated stderr (the cppcheck XML report).

    Returns:
        A SubprocessResult instance.
    """
    return SubprocessResult(
        returncode=returncode,
        stdout="Checking a.c ...\n",
        stderr=stderr,
        output=stderr,
    )


def test_check_with_issues(cppcheck_plugin: CppcheckPlugin, tmp_path: Path) -> None:
    """Check returns issues parsed from the stderr XML report.

    Args:
        cppcheck_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    source = tmp_path / "a.c"
    source.write_text("int main(void){int v;return v;}\n")

    with (
        patch.object(cppcheck_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            cppcheck_plugin,
            "_run_subprocess_result",
            return_value=_subprocess_result(1, ISSUE_XML),
        ),
    ):
        mock_prepare.return_value = _ctx(tmp_path, source)
        result = cppcheck_plugin.check([str(source)], {})

    assert_that(result.name).is_equal_to(ToolName.CPPCHECK)
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.issues).is_not_none()
    issue = cast(CppcheckIssue, result.issues[0])  # type: ignore[index]
    assert_that(issue.code).is_equal_to("uninitvar")
    assert_that(issue.severity).is_equal_to("error")
    assert_that(issue.line).is_equal_to(11)


def test_check_clean(cppcheck_plugin: CppcheckPlugin, tmp_path: Path) -> None:
    """Check returns success with no issues for a clean file.

    Args:
        cppcheck_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    source = tmp_path / "a.c"
    source.write_text("int main(void){return 0;}\n")

    with (
        patch.object(cppcheck_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            cppcheck_plugin,
            "_run_subprocess_result",
            return_value=_subprocess_result(0, CLEAN_XML),
        ),
    ):
        mock_prepare.return_value = _ctx(tmp_path, source)
        result = cppcheck_plugin.check([str(source)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_execution_failure_fails_closed(
    cppcheck_plugin: CppcheckPlugin,
    tmp_path: Path,
) -> None:
    """A non-zero exit with no parseable findings fails closed.

    Args:
        cppcheck_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    source = tmp_path / "a.c"
    source.write_text("int main(void){return 0;}\n")

    with (
        patch.object(cppcheck_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            cppcheck_plugin,
            "_run_subprocess_result",
            return_value=_subprocess_result(2, "cppcheck: error: unknown option"),
        ),
    ):
        mock_prepare.return_value = _ctx(tmp_path, source)
        result = cppcheck_plugin.check([str(source)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).contains("unknown option")


def test_check_timeout_returns_failure(
    cppcheck_plugin: CppcheckPlugin,
    tmp_path: Path,
) -> None:
    """A subprocess timeout produces a failed ToolResult.

    Args:
        cppcheck_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    source = tmp_path / "a.c"
    source.write_text("int main(void){return 0;}\n")

    with (
        patch.object(cppcheck_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            cppcheck_plugin,
            "_run_subprocess_result",
            side_effect=subprocess.TimeoutExpired(cmd="cppcheck", timeout=60),
        ),
    ):
        mock_prepare.return_value = _ctx(tmp_path, source)
        result = cppcheck_plugin.check([str(source)], {})

    assert_that(result.success).is_false()
    assert_that(result.output).contains("timed out")


def test_check_skips_when_no_files(cppcheck_plugin: CppcheckPlugin) -> None:
    """Check returns the early result when preparation says to skip.

    Args:
        cppcheck_plugin: The plugin under test.
    """
    early = MagicMock()
    with patch.object(cppcheck_plugin, "_prepare_execution") as mock_prepare:
        ctx = MagicMock()
        ctx.should_skip = True
        ctx.early_result = early
        mock_prepare.return_value = ctx
        result = cppcheck_plugin.check(["."], {})

    assert_that(result).is_same_as(early)


def test_build_command_includes_defaults(cppcheck_plugin: CppcheckPlugin) -> None:
    """The default command enables the expected checks and XML output.

    Args:
        cppcheck_plugin: The plugin under test.
    """
    cmd = cppcheck_plugin._build_command(files=["a.c"])
    joined = " ".join(cmd)
    assert_that(joined).contains("--xml")
    assert_that(joined).contains("--quiet")
    assert_that(joined).contains("--error-exitcode=1")
    assert_that(joined).contains(
        "--enable=warning,style,performance,portability",
    )
    assert_that(cmd[-1]).is_equal_to("a.c")


def test_fix_raises_not_implemented(cppcheck_plugin: CppcheckPlugin) -> None:
    """Cppcheck does not support fixing.

    Args:
        cppcheck_plugin: The plugin under test.
    """
    assert_that(cppcheck_plugin.fix).raises(NotImplementedError).when_called_with(
        ["a.c"],
        {},
    )
