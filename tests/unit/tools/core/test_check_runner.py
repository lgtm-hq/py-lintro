"""Tests for the shared per-file check runner (issue #2311).

The runner's happy paths are exercised by the per-tool suites; these tests
cover the execution-failure and classification branches that a tool's own
tests do not reach, using the three definitions that drive the three policy
shapes: the exit-status-only default (shellcheck), a recorded failure message
(dotenv-linter) and ``issues_imply_failure`` (pydoclint).
"""

from __future__ import annotations

import subprocess  # nosec B404 - only TimeoutExpired is constructed here
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.tools.core.check_runner import PerFileCheckPolicy
from lintro.tools.definitions.dotenv_linter import DotenvLinterPlugin
from lintro.tools.definitions.pydoclint import PydoclintPlugin
from lintro.tools.definitions.shellcheck import ShellcheckPlugin

#: One shellcheck finding in the json1 format the plugin asks for.
SHELLCHECK_FINDING: str = """[
  {
    "file": "script.sh",
    "line": 2,
    "endLine": 2,
    "column": 6,
    "endColumn": 10,
    "level": "warning",
    "code": 2086,
    "message": "Double quote to prevent globbing and word splitting."
  }
]"""

#: One dotenv-linter finding in its default text format.
DOTENV_FINDING: str = ".env:1 LowercaseKey: The foo key should be in uppercase\n"

#: One pydoclint finding in its indented text format.
PYDOCLINT_FINDING: str = "module.py\n    10: DOC101: Docstring is missing arguments.\n"


@pytest.fixture(autouse=True)
def _stub_version_check() -> Iterator[None]:
    """Stub the version precheck for the whole test, not just construction.

    Yields:
        None: While ``verify_tool_version`` is patched out.
    """
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        yield


@pytest.fixture
def shell_script(tmp_path: Path) -> Path:
    """Write a shell script for the shellcheck-backed cases.

    Args:
        tmp_path: Temporary directory for the file.

    Returns:
        Path to the created script.
    """
    script = tmp_path / "script.sh"
    script.write_text("#!/bin/bash\necho $foo\n")
    return script


def test_a_clean_run_reports_success_and_no_issues(shell_script: Path) -> None:
    """A zero exit with no findings is a passing file.

    Args:
        shell_script: Shell script the runner is pointed at.
    """
    plugin = ShellcheckPlugin()
    with patch.object(
        plugin,
        "_run_subprocess",
        return_value=(True, "[]"),
    ) as run_subprocess:
        result = plugin.check([str(shell_script)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    # The command builder must still receive the file: an argv that lost the
    # path would check the whole tree and still report a clean ToolResult.
    cmd = run_subprocess.call_args.kwargs["cmd"]
    assert_that(cmd[0]).is_equal_to("shellcheck")
    assert_that(cmd[-1]).is_equal_to(str(shell_script))


def test_findings_are_collected_and_fail_the_run(shell_script: Path) -> None:
    """Parsed findings are surfaced on the ToolResult and fail the run.

    Args:
        shell_script: Shell script the runner is pointed at.
    """
    plugin = ShellcheckPlugin()
    with patch.object(
        plugin,
        "_run_subprocess",
        return_value=(False, SHELLCHECK_FINDING),
    ):
        result = plugin.check([str(shell_script)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.issues).is_length(1)


def test_a_timeout_marks_the_file_skipped_and_the_run_timed_out(
    shell_script: Path,
) -> None:
    """A subprocess timeout stays distinguishable from a genuine finding.

    Args:
        shell_script: Shell script the runner is pointed at.
    """
    plugin = ShellcheckPlugin()
    with patch.object(
        plugin,
        "_run_subprocess",
        side_effect=subprocess.TimeoutExpired(cmd=["shellcheck"], timeout=1),
    ):
        result = plugin.check([str(shell_script)], {})

    assert_that(result.success).is_false()
    assert_that(result.timed_out).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_an_execution_error_is_reported_as_a_failure(shell_script: Path) -> None:
    """An OS error while checking fails the file and surfaces the message.

    Args:
        shell_script: Shell script the runner is pointed at.
    """
    plugin = ShellcheckPlugin()
    with patch.object(
        plugin,
        "_run_subprocess",
        side_effect=OSError("no shellcheck"),
    ):
        result = plugin.check([str(shell_script)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).contains("no shellcheck")


def test_a_nonzero_exit_without_findings_stays_a_plain_failure(
    shell_script: Path,
) -> None:
    """Without a failure message the raw output is reported, not an error.

    Args:
        shell_script: Shell script the runner is pointed at.
    """
    plugin = ShellcheckPlugin()
    with patch.object(
        plugin,
        "_run_subprocess",
        return_value=(False, "shellcheck: unknown option"),
    ):
        result = plugin.check([str(shell_script)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).contains("unknown option")
    assert_that(result.output).does_not_contain("Error processing")


def test_a_failure_message_turns_an_empty_nonzero_exit_into_an_error(
    tmp_path: Path,
) -> None:
    """dotenv-linter's policy classifies a silent non-zero exit as an error.

    Args:
        tmp_path: Temporary directory for the dotenv file.
    """
    env_file = tmp_path / ".env"
    env_file.write_text("foo=1\n")
    plugin = DotenvLinterPlugin()
    with patch.object(plugin, "_run_subprocess", return_value=(False, "")):
        result = plugin.check([str(env_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).contains("dotenv-linter check failed")


def test_a_failure_message_does_not_hide_real_findings(tmp_path: Path) -> None:
    """A non-zero exit that did parse findings reports them, not the message.

    Args:
        tmp_path: Temporary directory for the dotenv file.
    """
    env_file = tmp_path / ".env"
    env_file.write_text("foo=1\n")
    plugin = DotenvLinterPlugin()
    with patch.object(
        plugin,
        "_run_subprocess",
        return_value=(False, DOTENV_FINDING),
    ):
        result = plugin.check([str(env_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.output).does_not_contain("dotenv-linter check failed")


def test_issues_imply_failure_fails_a_file_that_exited_zero(tmp_path: Path) -> None:
    """Pydoclint reports findings on a clean exit status; the file must fail.

    Args:
        tmp_path: Temporary directory for the Python file.
    """
    module = tmp_path / "module.py"
    module.write_text("def f():\n    return 1\n")
    plugin = PydoclintPlugin()
    with patch.object(
        plugin,
        "_run_subprocess",
        return_value=(True, PYDOCLINT_FINDING),
    ):
        result = plugin.check([str(module)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)


def test_the_default_policy_leaves_both_classification_knobs_off() -> None:
    """The default policy is the exit-status-only shape most tools want."""
    policy = PerFileCheckPolicy()

    assert_that(policy.failure_message).is_none()
    assert_that(policy.issues_imply_failure).is_false()
    assert_that(policy.label).is_equal_to("Processing files")


def test_a_parser_failure_fails_only_that_file(tmp_path: Path) -> None:
    """A malformed report must not abort the run for the remaining files.

    Args:
        tmp_path: Temporary directory for the shell scripts.
    """
    first = tmp_path / "a.sh"
    first.write_text("#!/bin/bash\necho $foo\n")
    second = tmp_path / "b.sh"
    second.write_text("#!/bin/bash\necho $bar\n")
    plugin = ShellcheckPlugin()
    with (
        patch.object(plugin, "_run_subprocess", return_value=(True, "[]")),
        patch(
            "lintro.tools.definitions.shellcheck.parse_shellcheck_output",
            side_effect=[ValueError("malformed report"), []],
        ),
    ):
        result = plugin.check([str(first), str(second)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).contains("malformed report")
