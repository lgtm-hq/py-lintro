"""The per-file fix runner keeps stage errors through a clean retry (#3401).

A file whose initial check or fix command recorded an execution error
measured nothing for that file. The runner must report the run as an
unmeasured execution failure, so the convergence retry stops on it instead of
letting a clean second pass replace it. The real shfmt plugin drives the
runner with canned subprocess results.
"""

from __future__ import annotations

import subprocess  # nosec B404 - TimeoutExpired for a mocked re-check
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.models.core.subprocess_result import SubprocessResult
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.dotenv_linter.dotenv_linter_parser import (
    parse_dotenv_linter_output,
)
from lintro.tools.core.fix_runner import (
    FixStage,
    FixStageError,
    PerFileFixPolicy,
    VerifyMode,
    run_per_file_fix,
)
from lintro.tools.dotenv_linter.definition import DotenvLinterPlugin
from lintro.tools.shfmt.definition import ShfmtPlugin
from lintro.utils.execution.fix_retry import run_fix_with_retry
from tests.unit.tools.subprocess_helpers import subprocess_result

_SHFMT_DIFF = """--- script.sh.orig
+++ script.sh
@@ -1,2 +1,2 @@
 #!/bin/bash
-echo  "hi"
+echo "hi"
"""
_CLEAN = subprocess_result(True, "")


@pytest.fixture(autouse=True)
def _stub_version_check() -> Iterator[None]:
    """Keep the version precheck out of the canned subprocess sequence.

    Yields:
        None: While ``verify_tool_version`` is patched out.
    """
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        yield


@pytest.mark.parametrize(
    ("failed_pass", "initial_count", "kept_findings", "message"),
    [
        (
            [SubprocessResult(2, "", "shfmt: bad flag", "shfmt: bad flag")],
            None,
            0,
            "check failed before fix",
        ),
        ([OSError("no shfmt")], None, 0, "no shfmt"),
        (
            [subprocess_result(False, _SHFMT_DIFF), OSError("write failed")],
            1,
            1,
            "write failed",
        ),
        (
            [
                subprocess_result(False, _SHFMT_DIFF),
                SubprocessResult(
                    1,
                    "",
                    "open .script.sh123: permission denied\n",
                    "open .script.sh123: permission denied\n",
                ),
            ],
            1,
            1,
            "Fix command failed (exit code 1)",
        ),
        (
            [subprocess_result(False, _SHFMT_DIFF), SubprocessResult(-9, "", "", "")],
            1,
            1,
            "Fix command was terminated by signal 9",
        ),
    ],
    ids=[
        "initial-check-exits-without-verdict",
        "initial-check-raises",
        "fix-raises",
        "fix-permission-denied",
        "fix-killed",
    ],
)
def test_errored_stage_then_clean_retry_stays_unmeasured(
    tmp_path: Path,
    failed_pass: list[SubprocessResult | Exception],
    initial_count: int | None,
    kept_findings: int,
    message: str,
) -> None:
    """The errored file keeps its findings; the fix counts stay unknown."""
    script = tmp_path / "script.sh"
    script.write_text('#!/bin/bash\necho  "hi"\n')
    plugin = ShfmtPlugin()
    with patch.object(
        plugin,
        "_run_subprocess_result",
        side_effect=[*failed_pass, _CLEAN, _CLEAN],
    ):
        result = run_fix_with_retry(plugin, [str(script)], {}, 3)

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.residual_unknown).is_true()
    assert_that(result.initial_issues_count).is_equal_to(initial_count)
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
    assert_that(result.issues).is_length(kept_findings)
    assert_that(result.output).contains(message)


def test_one_errored_file_leaves_the_whole_run_unmeasured(tmp_path: Path) -> None:
    """A clean file beside an errored one cannot make the run measured."""
    clean = tmp_path / "a.sh"
    broken = tmp_path / "b.sh"
    for script in (clean, broken):
        script.write_text("#!/bin/bash\necho hi\n")
    plugin = ShfmtPlugin()
    with patch.object(
        plugin,
        "_run_subprocess_result",
        side_effect=[_CLEAN, OSError("b.sh: permission denied")],
    ):
        result = plugin.fix([str(clean), str(broken)], {})

    assert_that(result.execution_failed).is_true()
    assert_that(result.residual_unknown).is_true()
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.outcome.accounting.initial).is_none()
    assert_that(result.output).contains("permission denied")


_DOTENV_FINDING = ".env:1 LowercaseKey: The foo key should be in uppercase\n"


def test_dotenv_linter_permission_denied_then_clean_retry_stays_unmeasured(
    tmp_path: Path,
) -> None:
    """dotenv-linter exits 1 when it cannot write, which is not a finding."""
    env_file = tmp_path / ".env"
    env_file.write_text("foo=1\n")
    plugin = DotenvLinterPlugin()
    denied = (
        'Error: Os { code: 13, kind: PermissionDenied, message: "Permission denied" }'
    )
    with patch.object(
        plugin,
        "_run_subprocess_result",
        side_effect=[
            subprocess_result(False, _DOTENV_FINDING),
            SubprocessResult(1, "Fixing .env\n", denied, f"Fixing .env\n{denied}"),
            _CLEAN,
            _CLEAN,
        ],
    ):
        result = run_fix_with_retry(plugin, [str(env_file)], {}, 3)

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
    assert_that(result.issues).extracting("code").is_equal_to(["LowercaseKey"])


def test_declared_findings_exit_stays_measured_and_reports_its_stage(
    tmp_path: Path,
) -> None:
    """Only an undeclared exit is an error, and the caller learns its stage."""
    env_file = tmp_path / ".env"
    env_file.write_text("foo=1\n")
    plugin = DotenvLinterPlugin()
    ctx = plugin.prepare(paths=[str(env_file)], options={}, no_files_message="none")
    assert not isinstance(ctx, ToolResult)

    def run(exit_codes: frozenset[int], errors: list[FixStageError]) -> ToolResult:
        with patch.object(
            plugin,
            "_run_subprocess_result",
            side_effect=[
                subprocess_result(False, _DOTENV_FINDING),
                SubprocessResult(3, "", "", ""),
            ],
        ):
            return run_per_file_fix(
                ctx,
                plugin=plugin,
                check_command=plugin._check_command,
                fix_command=plugin._fix_command,
                parse=lambda output: parse_dotenv_linter_output(output=output),
                policy=PerFileFixPolicy(
                    check_failure_message="dotenv-linter check failed",
                    fix_findings_exit_codes=exit_codes,
                ),
                errors=errors,
            )

    declared_errors: list[FixStageError] = []
    declared = run(frozenset({3}), declared_errors)
    undeclared_errors: list[FixStageError] = []
    undeclared = run(frozenset(), undeclared_errors)

    assert_that(declared.execution_failed).is_false()
    assert_that(declared.fixed_issues_count).is_zero()
    assert_that(declared.remaining_issues_count).is_equal_to(1)
    assert_that(declared_errors).is_empty()
    assert_that(undeclared.execution_failed).is_true()
    assert_that(undeclared.remaining_issues_count).is_none()
    assert_that(undeclared_errors).extracting("stage", "message").is_equal_to(
        [(FixStage.FIX, "Fix command failed (exit code 3)")],
    )


def test_verification_timeout_is_a_timeout_not_an_execution_failure(
    tmp_path: Path,
) -> None:
    """A re-check that times out reads like a fix-command timeout."""
    env_file = tmp_path / ".env"
    env_file.write_text("foo=1\n")
    plugin = DotenvLinterPlugin()
    ctx = plugin.prepare(paths=[str(env_file)], options={}, no_files_message="none")
    assert not isinstance(ctx, ToolResult)
    with patch.object(
        plugin,
        "_run_subprocess_result",
        side_effect=[
            subprocess_result(False, _DOTENV_FINDING),
            _CLEAN,
            subprocess.TimeoutExpired(cmd=["dotenv-linter"], timeout=1),
        ],
    ):
        result = run_per_file_fix(
            ctx,
            plugin=plugin,
            check_command=plugin._check_command,
            fix_command=plugin._fix_command,
            parse=lambda output: parse_dotenv_linter_output(output=output),
            policy=PerFileFixPolicy(
                check_failure_message="dotenv-linter check failed",
                verify=VerifyMode.AFTER_SUCCESS,
                verify_failure_message="dotenv-linter recheck failed",
            ),
        )

    assert_that(result.timed_out).is_true()
    assert_that(result.execution_failed).is_false()
    assert_that(result.residual_unknown).is_true()
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.issues).is_length(1)


@pytest.mark.parametrize(
    ("fix", "failed"),
    [
        (SubprocessResult(0, "", "Traceback (most recent call last):\n", ""), False),
        (SubprocessResult(1, "PermissionError: ENOSPC.env\n", "", ""), False),
        (SubprocessResult(1, "", "warning: see PermissionError.env\n", ""), False),
        (SubprocessResult(1, "", "PermissionError: [Errno 13] denied\n", ""), True),
        (SubprocessResult(1, "", "Traceback (most recent call last):\n", ""), True),
    ],
    ids=[
        "exit-0-ignores-output",
        "stdout-is-never-read",
        "mid-line-name-is-not-a-diagnostic",
        "exception-line-on-stderr",
        "traceback-header-on-stderr",
    ],
)
def test_declared_findings_exit_reads_only_stderr_diagnostics(
    tmp_path: Path,
    fix: SubprocessResult,
    failed: bool,
) -> None:
    """Only a line-start diagnostic on stderr turns a findings exit into an error."""
    env_file = tmp_path / "PermissionError.env"
    env_file.write_text("foo=1\n")
    plugin = DotenvLinterPlugin()
    ctx = plugin.prepare(paths=[str(env_file)], options={}, no_files_message="none")
    assert not isinstance(ctx, ToolResult)
    with patch.object(
        plugin,
        "_run_subprocess_result",
        side_effect=[subprocess_result(False, _DOTENV_FINDING), fix],
    ):
        result = run_per_file_fix(
            ctx,
            plugin=plugin,
            check_command=plugin._check_command,
            fix_command=plugin._fix_command,
            parse=lambda output: parse_dotenv_linter_output(output=output),
            policy=PerFileFixPolicy(
                check_failure_message="dotenv-linter check failed",
                fix_findings_exit_codes=frozenset({1}),
            ),
        )

    assert_that(result.execution_failed).is_equal_to(failed)
    assert_that(result.residual_unknown).is_equal_to(failed)
    assert_that(result.initial_issues_count).is_equal_to(1)
    if not failed:
        expected_fixed = int(fix.returncode == 0)
        assert_that(result.fixed_issues_count).is_equal_to(expected_fixed)


_SPLIT_NAME = "query\nPermissionError: error permission denied.env"


@pytest.mark.parametrize(
    ("stderr", "failed"),
    [
        ("  == [{path}] ==\n  L:   1 | PRS | unparsable\n", False),
        ("Fixing {path}: error, permission denied\n", False),
        (
            "Traceback (most recent call last):\n"
            "PermissionError: [Errno 13] Permission denied: {path!r}\n",
            True,
        ),
        ("  == [{path}] ==\nPermissionError: [Errno 13] denied\n", True),
        ("{path}\n", True),
    ],
    ids=[
        "echoed-path-split-by-newline",
        "echoed-path-with-error-words",
        "traceback-naming-the-path",
        "exception-line-after-echoed-path",
        "path-echoed-at-line-start",
    ],
)
def test_echoed_multi_line_path_is_not_a_diagnostic(
    tmp_path: Path,
    stderr: str,
    failed: bool,
) -> None:
    """A mid-line echo of the file path is masked; real diagnostics are not (#3414).

    The name splits across lines into an exception-shaped fragment. Only an
    echo that starts mid-line is the tool printing the path, so a line that
    starts with the fragment is still read as a diagnostic: the runner fails
    closed rather than passing a failed write.
    """
    env_file = tmp_path / _SPLIT_NAME
    env_file.write_text("foo=1\n")
    plugin = DotenvLinterPlugin()
    ctx = plugin.prepare(paths=[str(env_file)], options={}, no_files_message="none")
    assert not isinstance(ctx, ToolResult)
    path = ctx.files[0]
    fix = SubprocessResult(1, "", stderr.format(path=path), "")
    errors: list[FixStageError] = []
    with patch.object(
        plugin,
        "_run_subprocess_result",
        side_effect=[subprocess_result(False, _DOTENV_FINDING), fix],
    ):
        result = run_per_file_fix(
            ctx,
            plugin=plugin,
            check_command=plugin._check_command,
            fix_command=plugin._fix_command,
            parse=lambda output: parse_dotenv_linter_output(output=output),
            errors=errors,
            policy=PerFileFixPolicy(
                check_failure_message="dotenv-linter check failed",
                fix_findings_exit_codes=frozenset({1}),
            ),
        )

    assert_that(path).contains("\nPermissionError: ")
    assert_that(result.execution_failed).is_equal_to(failed)
    assert_that(result.residual_unknown).is_equal_to(failed)
    assert_that([error.stage for error in errors]).is_equal_to(
        [FixStage.FIX] if failed else [],
    )
    if not failed:
        assert_that(result.fixed_issues_count).is_zero()
        assert_that(result.remaining_issues_count).is_equal_to(1)
