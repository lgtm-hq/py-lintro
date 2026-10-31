"""Tests for the shared batch check/fix runner (issue #2311).

The runner's happy paths are exercised by the per-tool suites; these tests
cover the classification and execution-failure branches that a tool's own
tests do not reach, using the definitions that drive the distinct policy
shapes: exit-status-only (clippy), issues-only (oxfmt) and the batch fix
pipeline (oxlint).
"""

from __future__ import annotations

import subprocess  # nosec B404 - only TimeoutExpired is constructed here
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.models.core.tool_result import ToolResult
from lintro.parsers.clippy.clippy_issue import ClippyIssue
from lintro.parsers.oxfmt.oxfmt_parser import parse_oxfmt_output
from lintro.plugins.base import BaseToolPlugin, PreparedExecution
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.clippy.definition import ClippyPlugin
from lintro.tools.core.batch_runner import (
    DEFAULT_BATCH_CHECK_POLICY,
    BatchCheckPolicy,
    BatchCommands,
    BatchFixPolicy,
    BatchOutput,
    BatchSuccess,
    batch_check_result,
    run_batch_check,
    run_batch_fix,
)
from lintro.tools.oxfmt.definition import OxfmtPlugin
from lintro.tools.oxlint.definition import OxlintPlugin
from lintro.tools.prettier.definition import PrettierPlugin

#: One oxfmt "needs formatting" line, in the ``--list-different`` format.
OXFMT_DIFFERENT: str = "app.ts\n"

#: One oxlint diagnostic in the JSON format the plugin asks for.
OXLINT_FINDING: str = """{
  "diagnostics": [
    {
      "filename": "app.ts",
      "message": "Unexpected debugger statement.",
      "severity": "error",
      "code": "eslint(no-debugger)",
      "labels": [{"span": {"offset": 0, "length": 9}}]
    }
  ]
}"""


@pytest.fixture(autouse=True)
def _stub_version_check() -> Iterator[None]:
    """Stub the version precheck so no real binary has to be present.

    Yields:
        None: While ``verify_tool_version`` is patched out.
    """
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        yield


@pytest.fixture
def ts_file(tmp_path: Path) -> Path:
    """Create a TypeScript file for the JS-family plugins to discover.

    Args:
        tmp_path: Temporary directory for the file.

    Returns:
        Path to the created file.
    """
    path = tmp_path / "app.ts"
    path.write_text("const x = 1\n")
    return path


@pytest.mark.parametrize(
    ("plugin_cls", "clean_output", "finding_output"),
    [
        (OxfmtPlugin, "checked 1\n", OXFMT_DIFFERENT),
        (OxlintPlugin, '{"diagnostics": []}', OXLINT_FINDING),
        (
            PrettierPlugin,
            "All matched files use Prettier code style!\n",
            "[warn] app.css\n",
        ),
    ],
    ids=["oxfmt", "oxlint", "prettier"],
)
@pytest.mark.parametrize(
    ("returncode", "report", "expected_success", "expected_count"),
    [
        (0, "clean", True, 0),
        (0, "findings", False, 1),
        (1, "findings", False, 1),
        (1, "error", False, 0),
        (1, "clean", False, 0),
        (1, "empty", False, 0),
        (2, "error", False, 0),
        (2, "findings", False, 1),
        (137, "empty", False, 0),
        (-11, "error", False, 0),
    ],
)
def test_issues_only_requires_a_clean_exit_without_findings(
    plugin_cls: type[BaseToolPlugin],
    clean_output: str,
    finding_output: str,
    returncode: int,
    report: str,
    expected_success: bool,
    expected_count: int,
    ts_file: Path,
) -> None:
    """All three definitions reject failed empty reports and retain their output.

    Args:
        plugin_cls: Definition whose real discovery and parser run.
        clean_output: Informational output without findings.
        finding_output: A single diagnostic in the tool's format.
        returncode: Process status, including errors and signal termination.
        report: Payload shape to simulate.
        expected_success: Expected public verdict.
        expected_count: Expected number of parsed findings.
        ts_file: File the plugin discovers.
    """
    ts_file.with_suffix(".css").write_text("a { color: red; }\n")
    output = {
        "clean": clean_output,
        "findings": finding_output,
        "error": "error: invalid configuration\n",
        "empty": "",
    }[report]
    plugin = plugin_cls()
    process = SubprocessResult(returncode, output, "", output)
    with patch.object(plugin, "_run_subprocess_result", return_value=process):
        result = plugin.check([str(ts_file.parent)], {})

    assert_that(result.name).is_equal_to(plugin.definition.name)
    assert_that(result.success).is_equal_to(expected_success)
    assert_that(result.issues_count).is_equal_to(expected_count)
    assert_that(result.inspected_files).is_equal_to(
        True if returncode == 0 or expected_count else None,
    )
    assert_that(len(result.issues or [])).is_equal_to(expected_count)
    assert_that(result.output).is_equal_to(None if expected_success else output)
    assert_that(result.cwd).is_equal_to(str(ts_file.parent))
    assert_that(result.skipped).is_false()


def test_a_check_timeout_is_reported_as_an_execution_failure(ts_file: Path) -> None:
    """A timeout never inflates the issue count.

    Args:
        ts_file: File the plugin discovers.
    """
    plugin = OxfmtPlugin()
    with patch.object(
        plugin,
        "_run_subprocess_result",
        side_effect=subprocess.TimeoutExpired(cmd=["oxfmt"], timeout=30),
    ):
        result = plugin.check([str(ts_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.timed_out).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_batch_fix_scores_the_difference_between_the_two_checks(
    ts_file: Path,
) -> None:
    """The fixed count is the drop between the pre-fix and post-fix checks.

    Args:
        ts_file: File the plugin discovers.
    """
    plugin = OxlintPlugin()
    with patch.object(
        plugin,
        "_run_subprocess_result",
        side_effect=[
            SubprocessResult(1, OXLINT_FINDING, "", OXLINT_FINDING),
            SubprocessResult(0, "", "", ""),
            SubprocessResult(0, '{"diagnostics": []}', "", '{"diagnostics": []}'),
        ],
    ):
        result = plugin.fix([str(ts_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_equal_to(1)
    assert_that(result.remaining_issues_count).is_equal_to(0)
    assert_that(result.output).contains("All issues were successfully auto-fixed")


@pytest.mark.parametrize("always_report", [False, True])
@pytest.mark.parametrize("initial_output", ["", OXFMT_DIFFERENT])
def test_deprecated_initial_issues_policy_keyword_is_a_noop(
    always_report: bool,
    initial_output: str,
    ts_file: Path,
) -> None:
    """Legacy plugin constructors keep working with either keyword value.

    Args:
        always_report: Deprecated keyword value supplied by an existing plugin.
        initial_output: Initial check output with zero or one finding.
        ts_file: File the batch formatter processes.
    """
    plugin = OxfmtPlugin()
    policy = BatchFixPolicy(always_report_initial_issues=always_report)
    with patch.object(
        plugin,
        "_run_subprocess_result",
        side_effect=[
            SubprocessResult(0, initial_output, "", initial_output),
            SubprocessResult(0, "", "", ""),
            SubprocessResult(0, "", "", ""),
        ],
    ):
        ctx = plugin.prepare([str(ts_file)], {})
        assert not isinstance(ctx, ToolResult)
        result = run_batch_fix(
            ctx,
            plugin=plugin,
            commands=BatchCommands(check=["oxfmt", "--check"], fix=["oxfmt"]),
            parse=lambda output: parse_oxfmt_output(output=output),
            policy=policy,
        )

    detected = parse_oxfmt_output(output=initial_output)
    assert_that(result.initial_issues).is_equal_to(detected)
    assert_that(result.initial_issues_count).is_equal_to(len(detected))
    assert_that(result.fixed_issues_count).is_equal_to(len(detected))
    assert_that(result.remaining_issues_count).is_zero()
    assert_that(result.issues).is_empty()
    assert_that(result.success).is_true()


def test_a_fix_timeout_keeps_the_pre_fix_issues(ts_file: Path) -> None:
    """Issues detected before the timeout are reported as still remaining.

    Args:
        ts_file: File the plugin discovers.
    """
    plugin = OxlintPlugin()
    with patch.object(
        plugin,
        "_run_subprocess_result",
        side_effect=[
            SubprocessResult(1, OXLINT_FINDING, "", OXLINT_FINDING),
            subprocess.TimeoutExpired(cmd=["oxlint"], timeout=30),
        ],
    ):
        result = plugin.fix([str(ts_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.timed_out).is_true()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.initial_issues_count).is_equal_to(1)


def test_a_recheck_finding_more_than_the_first_pass_stays_consistent(
    ts_file: Path,
) -> None:
    """A post-fix pass that surfaces extra findings keeps the counts valid.

    ``ToolResult`` rejects ``initial != fixed + remaining``, so the initial
    total grows to cover what the verification run actually reported instead
    of leaving the arithmetic remainder behind.

    Args:
        ts_file: File the plugin discovers.
    """
    plugin = OxfmtPlugin()
    with patch.object(
        plugin,
        "_run_subprocess_result",
        side_effect=[
            SubprocessResult(1, "app.ts\n", "", "app.ts\n"),
            SubprocessResult(0, "", "", ""),
            SubprocessResult(1, "app.ts\nother.ts\n", "", "app.ts\nother.ts\n"),
        ],
    ):
        result = plugin.fix([str(ts_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.remaining_issues_count).is_equal_to(2)
    assert_that(result.fixed_issues_count).is_equal_to(0)
    assert_that(result.initial_issues_count).is_equal_to(2)


@pytest.mark.parametrize(
    ("plugin_cls", "finding_output"),
    [
        (OxfmtPlugin, OXFMT_DIFFERENT),
        (OxlintPlugin, OXLINT_FINDING),
        (PrettierPlugin, "[warn] app.css\n"),
    ],
    ids=["oxfmt", "oxlint", "prettier"],
)
@pytest.mark.parametrize("failed_stage", [0, 1, 2], ids=["precheck", "fix", "verify"])
@pytest.mark.parametrize(
    ("returncode", "failure_output"),
    [
        (1, ""),
        (2, ""),
        (2, "error: invalid configuration\n"),
        (137, ""),
        (-11, "error: process crashed\n"),
    ],
)
def test_batch_fix_stops_on_any_failed_stage_without_parsed_issues(
    plugin_cls: type[BaseToolPlugin],
    finding_output: str,
    failed_stage: int,
    returncode: int,
    failure_output: str,
    ts_file: Path,
) -> None:
    """A later clean report cannot erase an earlier execution failure.

    Args:
        plugin_cls: Tool whose real discovery and parser run.
        finding_output: A pre-fix finding in the tool's output format.
        failed_stage: Index of the pre-check, fix or verification stage.
        returncode: Execution failure status to preserve.
        failure_output: Unparseable or empty error report.
        ts_file: File the plugin discovers.
    """
    ts_file.with_suffix(".css").write_text("a { color: red; }\n")
    plugin = plugin_cls()
    processes = [
        SubprocessResult(1, finding_output, "", finding_output),
        SubprocessResult(0, "", "", ""),
        SubprocessResult(0, "", "", ""),
    ]
    processes[failed_stage] = SubprocessResult(
        returncode,
        "",
        failure_output,
        failure_output,
    )
    with patch.object(plugin, "_run_subprocess_result", side_effect=processes):
        result = plugin.fix([str(ts_file.parent)], {})

    expected_remaining = int(failed_stage > 0)
    assert_that(result.success).is_false()
    assert_that(result.output).is_equal_to(failure_output)
    assert_that(result.issues_count).is_equal_to(expected_remaining)
    assert_that(len(result.issues or [])).is_equal_to(expected_remaining)
    assert_that(result.initial_issues_count).is_equal_to(expected_remaining)
    assert_that(result.remaining_issues_count).is_none()
    assert_that(result.residual_unknown).is_true()
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.cwd).is_equal_to(str(ts_file.parent))
    assert_that(result.timed_out).is_false()


@pytest.mark.parametrize("failed_stage", [0, 1, 2])
def test_an_unexpected_fix_stage_exit_preserves_the_report_as_unknown(
    failed_stage: int,
    ts_file: Path,
) -> None:
    """Exit 2 is an execution error even if its payload includes a finding.

    Args:
        failed_stage: Pipeline stage returning the unexpected status.
        ts_file: File the plugin discovers.
    """
    plugin = OxfmtPlugin()
    processes = [SubprocessResult(0, "", "", "")] * 3
    processes[failed_stage] = SubprocessResult(2, OXFMT_DIFFERENT, "", OXFMT_DIFFERENT)
    with patch.object(plugin, "_run_subprocess_result", side_effect=processes):
        result = plugin.fix([str(ts_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.output).is_equal_to(OXFMT_DIFFERENT)
    assert_that(result.issues_count).is_equal_to(int(failed_stage == 0))
    assert_that(result.residual_unknown).is_true()
    assert_that(result.fixed_issues_count).is_none()


@pytest.mark.parametrize("failed_stage", [0, 1, 2], ids=["precheck", "fix", "verify"])
@pytest.mark.parametrize("initial_count", [0, 3])
@pytest.mark.parametrize("report_initial", [False, True])
def test_failed_fix_preserves_initial_accounting_and_reporting(
    failed_stage: int,
    initial_count: int,
    report_initial: bool,
    ts_file: Path,
) -> None:
    """A failed stage's findings cannot replace the pre-fix count or list policy.

    Args:
        failed_stage: Stage returning one finding and an execution error.
        initial_count: Number of findings from a successful pre-check.
        report_initial: Whether to include initial findings in the issue list.
        ts_file: File identifying the execution directory.
    """
    initial_output = "app.ts\nsecond.ts\nthird.ts\n" if initial_count else ""
    initial_issues = parse_oxfmt_output(initial_output)
    stage_issues = parse_oxfmt_output(OXFMT_DIFFERENT)
    processes = [
        SubprocessResult(int(initial_count > 0), initial_output, "", initial_output),
        SubprocessResult(0, "", "", ""),
        SubprocessResult(0, "", "", ""),
    ]
    processes[failed_stage] = SubprocessResult(2, OXFMT_DIFFERENT, "", OXFMT_DIFFERENT)
    plugin = OxfmtPlugin()
    with patch.object(plugin, "_run_subprocess_result", side_effect=processes):
        result = run_batch_fix(
            PreparedExecution(
                files=[str(ts_file)],
                rel_files=["app.ts"],
                cwd=str(ts_file.parent),
                timeout=plugin._get_effective_timeout(),
            ),
            plugin=plugin,
            commands=BatchCommands(check=["oxfmt", "--list-different"], fix=["oxfmt"]),
            parse=parse_oxfmt_output,
            policy=BatchFixPolicy(
                findings_exit_codes=(1,),
                report_initial_issues=report_initial,
            ),
        )

    expected_initial = stage_issues if failed_stage == 0 else initial_issues
    assert_that(result.success).is_false()
    assert_that(result.output).is_equal_to(OXFMT_DIFFERENT)
    assert_that(result.initial_issues_count).is_equal_to(len(expected_initial))
    assert_that(result.initial_issues).is_equal_to(
        expected_initial,
    )
    assert_that(result.remaining_issues_count).is_none()
    assert_that(result.issues_count).is_equal_to(len(expected_initial))
    assert_that(result.issues).is_equal_to(expected_initial)
    assert_that(result.residual_unknown).is_true()
    assert_that(result.fixed_issues_count).is_none()


def test_prettier_keeps_mixed_warnings_and_errors(ts_file: Path) -> None:
    """A Prettier error retains its raw report alongside real style findings.

    Args:
        ts_file: File used to locate the CSS fixture.
    """
    css_file = ts_file.with_suffix(".css")
    css_file.write_text("a {color:red}")
    output = (
        "[warn] app.css\n"
        "[error] Invalid configuration for file app.css:\n"
        "[error] Unexpected token in .prettierrc.json\n"
    )
    plugin = PrettierPlugin()
    with patch.object(
        plugin,
        "_run_subprocess_result",
        return_value=SubprocessResult(2, "", output, output),
    ):
        result = plugin.check([str(css_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.issues[0].file).is_equal_to("app.css")
    assert_that(result.output).is_equal_to(output)


def test_oxfmt_multiline_configuration_error_is_not_a_file(ts_file: Path) -> None:
    """A diagnostic containing a source path must not count as formatting work.

    Args:
        ts_file: File the plugin discovers.
    """
    output = (
        "Failed to load configuration file.\n"
        "  × Failed to parse config file: .oxfmtrc.json\n"
        "  ╰─▶ JSON parse error: trailing comma at line 3 column 1\n"
        "error: Failed to format app.ts\n"
    )
    plugin = OxfmtPlugin()
    with patch.object(
        plugin,
        "_run_subprocess_result",
        return_value=SubprocessResult(1, "", output, output),
    ):
        result = plugin.check([str(ts_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.issues).is_empty()
    assert_that(result.output).is_equal_to(output)


@pytest.mark.parametrize("success_policy", list(BatchSuccess))
def test_batch_check_keeps_timeout_warning_and_tool_name(
    success_policy: BatchSuccess,
) -> None:
    """Both execution APIs retain the same enhanced timeout diagnostics.

    Args:
        success_policy: Check policy selecting the subprocess API.
    """
    plugin = OxfmtPlugin()
    ctx = PreparedExecution(
        files=["app.ts"],
        rel_files=["app.ts"],
        cwd="/tmp",
        timeout=0,
    )
    messages: list[str] = []
    sink = logger.add(lambda message: messages.append(str(message)), level="WARNING")
    try:
        with patch.object(
            plugin,
            "_run_subprocess_result",
            side_effect=subprocess.TimeoutExpired(cmd=["oxfmt"], timeout=0),
        ):
            result = run_batch_check(
                ctx,
                plugin=plugin,
                cmd=["oxfmt", "app.ts"],
                parse=lambda output: [],
                policy=BatchCheckPolicy(
                    success=success_policy,
                    tool_name="Formatter override",
                ),
            )
    finally:
        logger.remove(sink)

    assert_that(result.success).is_false()
    assert_that(result.timed_out).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).contains("Formatter override execution timed out (0s")
    assert_that("".join(messages)).contains(result.output)


#: One parsed finding, enough to exercise every "issues present" branch of the
#: policy matrix. The concrete issue type is irrelevant to the classification.
_ONE_ISSUE: tuple[ClippyIssue, ...] = (
    ClippyIssue(
        file="src/lib.rs",
        line=1,
        column=1,
        code="clippy::needless_return",
        message="unneeded `return` statement",
    ),
)


@pytest.mark.parametrize(
    ("success_policy", "exit_success", "issues", "expected_success"),
    [
        (BatchSuccess.ISSUES_ONLY, True, (), True),
        (BatchSuccess.ISSUES_ONLY, False, (), False),
        (BatchSuccess.ISSUES_ONLY, True, _ONE_ISSUE, False),
        (BatchSuccess.ISSUES_ONLY, False, _ONE_ISSUE, False),
        (BatchSuccess.EXIT_STATUS, True, (), True),
        (BatchSuccess.EXIT_STATUS, False, (), False),
        (BatchSuccess.EXIT_STATUS, True, _ONE_ISSUE, True),
        (BatchSuccess.EXIT_STATUS, False, _ONE_ISSUE, False),
        (BatchSuccess.EXIT_AND_ISSUES, True, (), True),
        (BatchSuccess.EXIT_AND_ISSUES, False, (), False),
        (BatchSuccess.EXIT_AND_ISSUES, True, _ONE_ISSUE, False),
        (BatchSuccess.EXIT_AND_ISSUES, False, _ONE_ISSUE, False),
    ],
)
def test_the_success_policy_decides_the_verdict(
    success_policy: BatchSuccess,
    exit_success: bool,
    issues: tuple[ClippyIssue, ...],
    expected_success: bool,
) -> None:
    """Every ``BatchSuccess`` member is exercised over both inputs it reads.

    Args:
        success_policy: Verdict rule under test.
        exit_success: Whether the command exited zero.
        issues: Findings the parser produced.
        expected_success: Verdict the rule must reach.
    """
    result = batch_check_result(
        plugin=ClippyPlugin(),
        exit_success=exit_success,
        output="raw",
        issues=issues,
        policy=BatchCheckPolicy(success=success_policy),
    )

    assert_that(result.success).is_equal_to(expected_success)
    assert_that(result.issues_count).is_equal_to(len(issues))


@pytest.mark.parametrize(
    ("returncode", "issues", "expected_output"),
    [
        (1, _ONE_ISSUE, None),
        (-11, _ONE_ISSUE, "raw"),
        (None, (), "raw"),
    ],
)
def test_issues_only_always_preserves_execution_failure_output(
    returncode: int | None,
    issues: tuple[ClippyIssue, ...],
    expected_output: str | None,
) -> None:
    """Only a declared findings exit with parsed issues may suppress output.

    Args:
        returncode: Numeric status or an unavailable status.
        issues: Findings extracted from the process output.
        expected_output: Raw output retained despite the NEVER output policy.
    """
    result = batch_check_result(
        plugin=ClippyPlugin(),
        exit_success=returncode == 0,
        returncode=returncode,
        output="raw",
        issues=issues,
        policy=BatchCheckPolicy(
            success=BatchSuccess.ISSUES_ONLY,
            findings_exit_codes=(1,),
            output=BatchOutput.NEVER,
        ),
    )

    assert_that(result.success).is_equal_to(returncode == 0)
    assert_that(result.output).is_equal_to(expected_output)
    assert_that(result.issues_count).is_equal_to(len(issues))
    assert_that(result.issues).is_equal_to(list(issues))


def test_issues_only_default_rejects_undeclared_findings_exit() -> None:
    """The empty findings-exit default cannot hide failed output."""
    result = batch_check_result(
        plugin=ClippyPlugin(),
        exit_success=False,
        returncode=1,
        output="undeclared findings exit",
        issues=_ONE_ISSUE,
        policy=BatchCheckPolicy(
            success=BatchSuccess.ISSUES_ONLY,
            output=BatchOutput.NEVER,
        ),
    )

    assert_that(result.success).is_false()
    assert_that(result.output).is_equal_to("undeclared findings exit")
    assert_that(result.issues).is_equal_to(list(_ONE_ISSUE))


def test_batch_fix_default_does_not_accept_findings_status(ts_file: Path) -> None:
    """A new batch fixer must declare findings exits before proceeding to writes.

    Args:
        ts_file: File named by the simulated pre-check finding.
    """
    plugin = OxfmtPlugin()
    with patch.object(
        plugin,
        "_run_subprocess_result",
        return_value=SubprocessResult(1, OXFMT_DIFFERENT, "", OXFMT_DIFFERENT),
    ):
        result = run_batch_fix(
            PreparedExecution(
                files=[str(ts_file)],
                rel_files=["app.ts"],
                cwd=str(ts_file.parent),
                timeout=plugin._get_effective_timeout(),
            ),
            plugin=plugin,
            commands=BatchCommands(check=["oxfmt", "--list-different"], fix=["oxfmt"]),
            parse=lambda output: list(_ONE_ISSUE),
            policy=BatchFixPolicy(),
        )

    assert_that(result.success).is_false()
    assert_that(result.output).is_equal_to(OXFMT_DIFFERENT)
    assert_that(result.issues).is_equal_to(list(_ONE_ISSUE))
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.residual_unknown).is_true()


@pytest.mark.parametrize("returncode", [0, 2])
def test_numeric_status_overrides_a_conflicting_success_flag(returncode: int) -> None:
    """A provided numeric status is authoritative for all check policies.

    Args:
        returncode: Exit status intentionally inconsistent with the legacy flag.
    """
    result = batch_check_result(
        plugin=ClippyPlugin(),
        exit_success=returncode != 0,
        returncode=returncode,
        output="status is authoritative",
        issues=(),
    )

    assert_that(result.success).is_equal_to(returncode == 0)


@pytest.mark.parametrize(
    ("output_policy", "exit_success", "issues", "expected_shown"),
    [
        (BatchOutput.NEVER, True, (), False),
        (BatchOutput.NEVER, False, (), False),
        (BatchOutput.NEVER, True, _ONE_ISSUE, False),
        (BatchOutput.NEVER, False, _ONE_ISSUE, False),
        (BatchOutput.ON_FAILURE, True, (), False),
        (BatchOutput.ON_FAILURE, False, (), True),
        (BatchOutput.ON_FAILURE, True, _ONE_ISSUE, True),
        (BatchOutput.ON_FAILURE, False, _ONE_ISSUE, True),
        (BatchOutput.ON_EXIT_FAILURE_WITHOUT_ISSUES, True, (), False),
        (BatchOutput.ON_EXIT_FAILURE_WITHOUT_ISSUES, False, (), True),
        (BatchOutput.ON_EXIT_FAILURE_WITHOUT_ISSUES, True, _ONE_ISSUE, False),
        (BatchOutput.ON_EXIT_FAILURE_WITHOUT_ISSUES, False, _ONE_ISSUE, False),
        (BatchOutput.ON_ISSUES_OR_EXIT_FAILURE, True, (), False),
        (BatchOutput.ON_ISSUES_OR_EXIT_FAILURE, False, (), True),
        (BatchOutput.ON_ISSUES_OR_EXIT_FAILURE, True, _ONE_ISSUE, True),
        (BatchOutput.ON_ISSUES_OR_EXIT_FAILURE, False, _ONE_ISSUE, True),
    ],
)
def test_the_output_policy_decides_when_raw_output_survives(
    output_policy: BatchOutput,
    exit_success: bool,
    issues: tuple[ClippyIssue, ...],
    expected_shown: bool,
) -> None:
    """Every ``BatchOutput`` member is exercised over both inputs it reads.

    The verdict ``ON_FAILURE`` consults is the default one, so these cases
    pair each output rule with ``BatchSuccess.EXIT_AND_ISSUES``.

    Args:
        output_policy: Output rule under test.
        exit_success: Whether the command exited zero.
        issues: Findings the parser produced.
        expected_shown: Whether the raw output must survive onto the result.
    """
    result = batch_check_result(
        plugin=ClippyPlugin(),
        exit_success=exit_success,
        output="raw",
        issues=issues,
        policy=BatchCheckPolicy(output=output_policy),
    )

    expected_output = "raw" if expected_shown else None
    assert_that(result.output).is_equal_to(expected_output)


@pytest.mark.parametrize(
    ("exit_success", "issues", "expected_success", "expected_output"),
    [
        (True, (), True, None),
        (False, (), False, "raw"),
        (True, _ONE_ISSUE, False, "raw"),
        (False, _ONE_ISSUE, False, "raw"),
    ],
)
def test_the_default_policy_demands_a_clean_exit_and_no_findings(
    exit_success: bool,
    issues: tuple[ClippyIssue, ...],
    expected_success: bool,
    expected_output: str | None,
) -> None:
    """The policy a definition gets for free is strict and reports on failure.

    Args:
        exit_success: Whether the command exited zero.
        issues: Findings the parser produced.
        expected_success: Verdict the default rules must reach.
        expected_output: Raw output expected on the result.
    """
    result = batch_check_result(
        plugin=ClippyPlugin(),
        exit_success=exit_success,
        output="raw",
        issues=issues,
        policy=DEFAULT_BATCH_CHECK_POLICY,
    )

    assert_that(result.success).is_equal_to(expected_success)
    assert_that(result.output).is_equal_to(expected_output)
