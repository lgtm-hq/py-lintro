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

from lintro.parsers.clippy.clippy_issue import ClippyIssue
from lintro.tools.clippy.definition import ClippyPlugin
from lintro.tools.core.batch_runner import (
    DEFAULT_BATCH_CHECK_POLICY,
    BatchCheckPolicy,
    BatchOutput,
    BatchSuccess,
    batch_check_result,
)
from lintro.tools.definitions.oxfmt import OxfmtPlugin
from lintro.tools.definitions.oxlint import OxlintPlugin

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


def test_issues_only_ignores_a_non_zero_exit(ts_file: Path) -> None:
    """A formatter that exits non-zero to report diffs still counts them.

    Args:
        ts_file: File the plugin discovers.
    """
    plugin = OxfmtPlugin()
    with patch.object(
        plugin,
        "_run_subprocess",
        return_value=(False, OXFMT_DIFFERENT),
    ):
        result = plugin.check([str(ts_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.output).is_equal_to(OXFMT_DIFFERENT)


def test_issues_only_suppresses_output_on_a_clean_run(ts_file: Path) -> None:
    """Informational output is dropped when nothing was found.

    Args:
        ts_file: File the plugin discovers.
    """
    plugin = OxfmtPlugin()
    with patch.object(plugin, "_run_subprocess", return_value=(True, "checked 1\n")):
        result = plugin.check([str(ts_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).is_none()


def test_a_check_timeout_is_reported_as_an_execution_failure(ts_file: Path) -> None:
    """A timeout never inflates the issue count.

    Args:
        ts_file: File the plugin discovers.
    """
    plugin = OxfmtPlugin()
    with patch.object(
        plugin,
        "_run_subprocess",
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
        "_run_subprocess",
        side_effect=[
            (False, OXLINT_FINDING),
            (True, ""),
            (True, '{"diagnostics": []}'),
        ],
    ):
        result = plugin.fix([str(ts_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_equal_to(1)
    assert_that(result.remaining_issues_count).is_equal_to(0)
    assert_that(result.output).contains("All issues were successfully auto-fixed")


def test_a_fix_timeout_keeps_the_pre_fix_issues(ts_file: Path) -> None:
    """Issues detected before the timeout are reported as still remaining.

    Args:
        ts_file: File the plugin discovers.
    """
    plugin = OxlintPlugin()
    with patch.object(
        plugin,
        "_run_subprocess",
        side_effect=[
            (False, OXLINT_FINDING),
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
        "_run_subprocess",
        side_effect=[
            (False, "app.ts\n"),
            (True, ""),
            (False, "app.ts\nother.ts\n"),
        ],
    ):
        result = plugin.fix([str(ts_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.remaining_issues_count).is_equal_to(2)
    assert_that(result.fixed_issues_count).is_equal_to(0)
    assert_that(result.initial_issues_count).is_equal_to(2)


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
        (BatchSuccess.ISSUES_ONLY, False, (), True),
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
