"""golangci-lint runs without a complete report (#3352).

The subprocess boundary is patched with what a current golangci-lint v2
really prints: its JSON report on stdout, its logs and errors on stderr.
"""

from __future__ import annotations

import subprocess  # nosec B404 - CompletedProcess builds mocked tool output
from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.models.core.tool_outcome import FixMeasured, TimedOut, ToolOutcome
from lintro.models.core.tool_result import ToolResult
from lintro.tools.golangci_lint.definition import GolangciLintPlugin, _merge_fix_results
from tests.unit.tools.golangci_lint.conftest import GOLANGCI_JSON_ONE_ISSUE

# golangci-lint v2 with an unparseable .golangci.yml: exit 3, no stdout.
_CONFIG_ERROR = (
    "Error: can't load config: can't read viper config: While parsing config: "
    "yaml: line 1: did not find expected node content\n"
    "The command is terminated due to an error: can't load config\n"
)


def _module(root: Path) -> Path:
    """Write a minimal Go module under ``root``.

    Args:
        root: Directory to populate.

    Returns:
        The module directory.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "go.mod").write_text("module example.com/m\n\ngo 1.22\n")
    (root / "main.go").write_text("package main\n\nfunc main() {}\n")
    return root


def _completed(
    returncode: int,
    stdout: str,
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    """Build a finished golangci-lint process.

    Args:
        returncode: Exit status.
        stdout: Standard output.
        stderr: Standard error.

    Returns:
        A CompletedProcess for ``subprocess.run`` to return.
    """
    return subprocess.CompletedProcess(["golangci-lint"], returncode, stdout, stderr)


def test_config_error_is_an_execution_failure_not_a_parse_failure(
    golangci_lint_plugin: GolangciLintPlugin,
    tmp_path: Path,
) -> None:
    """A failed exit with only stderr text names golangci-lint's own error."""
    _module(tmp_path)
    with patch("subprocess.run", return_value=_completed(3, "", _CONFIG_ERROR)):
        result = golangci_lint_plugin.check([str(tmp_path)], {})

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.inspected_files).is_none()
    assert_that(result.parse_failures_count).is_none()
    assert_that(result.issues).is_empty()
    assert_that(result.output).starts_with(
        "golangci-lint exited with an error and printed no report: "
        "Error: can't load config",
    )


def test_config_error_aborts_fix_with_an_unmeasured_residual(
    golangci_lint_plugin: GolangciLintPlugin,
    tmp_path: Path,
) -> None:
    """A fix whose initial check could not run measures nothing."""
    _module(tmp_path)
    with patch(
        "subprocess.run",
        return_value=_completed(3, "", _CONFIG_ERROR),
    ) as run:
        result = golangci_lint_plugin.fix([str(tmp_path)], {})

    assert_that(run.call_count).is_equal_to(1)
    assert_that(result.execution_failed).is_true()
    assert_that(result.inspected_files).is_none()
    assert_that(result.parse_failures_count).is_none()
    assert_that(result.residual_unknown_reason).contains("can't load config")
    assert_that(result.initial_issues_count).is_none()


def test_malformed_exit_zero_report_claims_no_inspection(
    golangci_lint_plugin: GolangciLintPlugin,
    tmp_path: Path,
) -> None:
    """A malformed report keeps its real finding and names the gap."""
    _module(tmp_path)
    report = (
        '{"Issues":[{"FromLinter":"errcheck","Text":"kept",'
        '"Pos":{"Filename":"main.go","Line":1,"Column":1}},null]}'
    )
    with patch("subprocess.run", return_value=_completed(0, report)):
        result = golangci_lint_plugin.check([str(tmp_path)], {})

    assert_that(result.success).is_false()
    assert_that(result.inspected_files).is_none()
    assert_that(result.execution_failed).is_true()
    assert_that(result.parse_failures_count).is_equal_to(1)
    assert_that(result.issues).extracting("message").is_equal_to(["kept"])
    assert_that(result.output).does_not_contain('"Issues"')
    assert_that(result.output).contains("could not be fully parsed")


def test_concurrency_exit_with_valid_report_quotes_the_message(
    golangci_lint_plugin: GolangciLintPlugin,
    tmp_path: Path,
) -> None:
    """Exit 3 beside a valid empty report fails with zero parse failures."""
    _module(tmp_path)
    with patch(
        "subprocess.run",
        return_value=_completed(
            3,
            '{"Issues":[],"Report":{}}',
            "parallel golangci-lint is running\n",
        ),
    ):
        result = golangci_lint_plugin.check([str(tmp_path)], {})

    assert_that(result.execution_failed).is_true()
    assert_that(result.inspected_files).is_none()
    assert_that(result.parse_failures_count).is_zero()
    assert_that(result.output).is_equal_to(
        "golangci-lint exited with an error and reported no findings: "
        "parallel golangci-lint is running",
    )


def test_array_like_stderr_log_after_report_keeps_findings(
    golangci_lint_plugin: GolangciLintPlugin,
    tmp_path: Path,
) -> None:
    """golangci-lint's ``-v`` log lines go to stderr, after the report."""
    _module(tmp_path)
    log = (
        'level=info msg="[config_reader] Config search paths: [./ /tmp /]"\n'
        'level=info msg="[runner] linters took 1s"\n'
    )
    with patch(
        "subprocess.run",
        return_value=_completed(1, GOLANGCI_JSON_ONE_ISSUE, log),
    ):
        result = golangci_lint_plugin.check([str(tmp_path)], {})

    assert_that(result.execution_failed).is_false()
    assert_that(result.parse_failures_count).is_zero()
    assert_that(result.inspected_files).is_true()
    assert_that(result.issues).extracting("code").is_equal_to(["errcheck"])


def test_multi_module_check_names_the_failing_module(
    golangci_lint_plugin: GolangciLintPlugin,
    tmp_path: Path,
) -> None:
    """With several modules the reason names the one that failed."""
    clean = _module(tmp_path / "a")
    broken = _module(tmp_path / "b")

    def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if Path(str(kwargs["cwd"])).resolve() == broken.resolve():
            return _completed(3, "", _CONFIG_ERROR)
        return _completed(1, GOLANGCI_JSON_ONE_ISSUE)

    with patch("subprocess.run", side_effect=run):
        result = golangci_lint_plugin.check([str(tmp_path)], {})

    assert_that(result.execution_failed).is_true()
    assert_that(result.inspected_files).is_none()
    assert_that(result.issues).extracting("file").is_equal_to(
        [str(clean.resolve() / "main.go")],
    )
    assert_that(result.output).starts_with(f"{broken.resolve()}: golangci-lint")


def test_merge_keeps_an_unmeasured_module_residual() -> None:
    """A direct caller of the merge sees the unknown residual and its reason."""
    measured = ToolResult.from_outcome(
        ToolOutcome(
            execution=TimedOut("module a timed out"),
            accounting=FixMeasured(initial=2, fixed=0, remaining=2),
            issues_count=2,
        ),
        name="golangci_lint",
    )
    unmeasured = ToolResult(
        name="golangci_lint",
        initial_issues_count=1,
        residual_unknown=True,
        residual_unknown_reason="golangci-lint printed no report.",
        execution_failed=True,
        inspected_files=None,
    )

    result = _merge_fix_results(name="golangci_lint", results=[measured, unmeasured])

    assert_that(result.timed_out).is_true()
    assert_that(result.residual_unknown).is_true()
    assert_that(result.residual_unknown_reason).is_equal_to(
        "golangci-lint printed no report.",
    )
    assert_that(result.initial_issues_count).is_equal_to(3)
    assert_that(result.remaining_issues_count).is_none()
    assert_that(result.execution_failed).is_true()
    assert_that(result.success).is_false()
