"""Unit tests for cppcheck plugin execution."""

from __future__ import annotations

import subprocess  # nosec B404 - only TimeoutExpired is constructed here
from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.enums.tool_name import ToolName
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.cppcheck.cppcheck_issue import CppcheckIssue
from lintro.plugins.base import ExecutionContext
from lintro.tools.cppcheck.definition import CppcheckPlugin

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


def _ctx(tmp_path: Path, file_path: Path) -> ExecutionContext:
    """Build a prepared execution context.

    Args:
        tmp_path: Temporary directory path.
        file_path: Source file included in the context.

    Returns:
        An ExecutionContext naming the single source file.
    """
    return ExecutionContext(
        files=[str(file_path)],
        rel_files=[file_path.name],
        cwd=str(tmp_path),
        timeout=60,
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
        patch.object(cppcheck_plugin, "prepare") as mock_prepare,
        patch.object(
            cppcheck_plugin,
            "_run_subprocess",
            return_value=(False, ISSUE_XML),
        ),
    ):
        mock_prepare.return_value = _ctx(tmp_path, source)
        result = cppcheck_plugin.check([str(source)], {})

    assert_that(result.name).is_equal_to(ToolName.CPPCHECK)
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.issues).is_not_none()
    issues = [i for i in (result.issues or []) if isinstance(i, CppcheckIssue)]
    assert_that(issues).is_not_empty()
    issue = issues[0]
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
        patch.object(cppcheck_plugin, "prepare") as mock_prepare,
        patch.object(
            cppcheck_plugin,
            "_run_subprocess",
            return_value=(True, CLEAN_XML),
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
        patch.object(cppcheck_plugin, "prepare") as mock_prepare,
        patch.object(
            cppcheck_plugin,
            "_run_subprocess",
            return_value=(False, "cppcheck: error: unknown option"),
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
        patch.object(cppcheck_plugin, "prepare") as mock_prepare,
        patch.object(
            cppcheck_plugin,
            "_run_subprocess",
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
    early = ToolResult(
        name="cppcheck",
        success=True,
        output="No files to check.",
        issues_count=0,
    )
    with patch.object(cppcheck_plugin, "prepare", return_value=early):
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


def test_check_passes_configured_options_into_the_subprocess_argv(
    cppcheck_plugin: CppcheckPlugin,
    tmp_path: Path,
) -> None:
    """Options set through ``check()`` reach the real cppcheck argv.

    The other execution tests assert command construction through the private
    ``_build_command`` helper, which cannot catch a ``check()`` that builds a
    command and then fails to pass it on. This spies the argv the subprocess
    layer actually receives.

    Args:
        cppcheck_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    source = tmp_path / "a.c"
    source.write_text("int main(void){return 0;}\n")
    seen: list[list[str]] = []

    def _spy(cmd: list[str], **kwargs: object) -> tuple[bool, str]:
        seen.append(cmd)
        return True, CLEAN_XML

    # Options reach a plugin through set_options (which is how ToolManager
    # drives it); check()'s dict only carries execution knobs such as timeout.
    # prepare() is left real so option merging and file discovery run for real:
    # a check() that stopped calling prepare, or built a command it never passed
    # on, would otherwise still pass. Only the version probe is stubbed, so the
    # test is hermetic — without that, a runner with no cppcheck on PATH takes
    # the skip path and never reaches _run_subprocess at all.
    cppcheck_plugin.set_options(
        enable=["warning", "style"],
        std="c11",
        inconclusive=True,
        suppress="missingInclude",
    )
    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(cppcheck_plugin, "_run_subprocess", side_effect=_spy),
    ):
        result = cppcheck_plugin.check([str(source)], {})

    assert_that(result.success).is_true()
    assert_that(seen).is_length(1)
    assert_that(seen[0]).contains(
        "--enable=warning,style",
        "--std=c11",
        "--inconclusive",
        "--suppress=missingInclude",
    )
    assert_that(seen[0][-1]).ends_with("a.c")
