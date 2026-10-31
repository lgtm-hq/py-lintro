"""Real-output controls for tool-side ignores and stream handling (#3000)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.enums.capability import Cap
from lintro.enums.verify_status import VerifyStatus
from lintro.models.core.tool_result import ToolResult
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.core.verify_pass import (
    VerifyOutcome,
    VerifyScope,
    fold_verify_results,
)
from lintro.tools.oxfmt.definition import OxfmtPlugin
from lintro.tools.oxlint.definition import OxlintPlugin
from lintro.tools.prettier.definition import PrettierPlugin
from lintro.utils.execution.fix_retry import run_fix_with_retry
from lintro.utils.json_output import serialize_tool_result

# Captured from oxlint 1.88.0 --format json app.ts with ignorePatterns: ["*.ts"].
OXLINT_NO_FILES = (
    "No files found to lint. Please check your paths and ignore patterns.\n"
    '{ "diagnostics": [],\n'
    '              "number_of_files": 0,\n'
    '              "number_of_rules": 96,\n'
    '              "threads_count": 4,\n'
    '              "start_time": 0.046569427\n'
    "            }\n            "
)
# Captured on stderr from oxfmt 0.73.0 --list-different / --write app.ts
# with *.ts in .prettierignore.
OXFMT_NO_FILES = (
    "Expected at least one target file. "
    "All matched files may have been excluded by ignore rules.\n"
)
OXLINT_FINDINGS = json.dumps(
    {
        "diagnostics": [
            {
                "filename": "app.ts",
                "message": "Unexpected debugger statement.",
                "severity": "error",
                "code": "eslint(no-debugger)",
                "labels": [],
            },
        ],
        "number_of_files": 1,
    },
)


@pytest.fixture
def batch_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Prepare real discoverable files without invoking a version probe.

    Args:
        tmp_path: Isolated test directory.
        monkeypatch: Fixture restoring the version gate.

    Returns:
        Directory containing targets for all three plugins.
    """
    (tmp_path / "app.ts").write_text("debugger;\n", encoding="utf-8")
    (tmp_path / "app.css").write_text("a{color:red}\n", encoding="utf-8")
    monkeypatch.setattr(
        "lintro.plugins.execution_preparation.verify_tool_version",
        lambda *args, **kwargs: None,
    )
    return tmp_path


@pytest.mark.parametrize("action", ["check", "fix"])
@pytest.mark.parametrize(
    ("plugin_cls", "returncode", "stdout", "stderr"),
    [
        (OxlintPlugin, 1, OXLINT_NO_FILES, ""),
        (OxfmtPlugin, 2, "", OXFMT_NO_FILES),
        (OxfmtPlugin, 2, OXFMT_NO_FILES, ""),
    ],
)
def test_ignored_targets_are_explicit_no_files(
    action: str,
    plugin_cls: type[BaseToolPlugin],
    returncode: int,
    stdout: str,
    stderr: str,
    batch_project: Path,
) -> None:
    """A genuine no-target response stops before writes and carries no verdict.

    Args:
        action: Check or fix entry point.
        plugin_cls: Tool that emitted the captured output.
        returncode: Tool's observed no-target exit code.
        stdout: Captured stdout.
        stderr: Captured stderr.
        batch_project: Discovered target directory.
    """
    plugin = plugin_cls()
    output = stdout + stderr
    with patch.object(
        plugin,
        "_run_subprocess_result",
        side_effect=[SubprocessResult(returncode, stdout, stderr, output)],
    ):
        result = getattr(plugin, action)([str(batch_project)], {})

    assert_that(result.success).is_true()
    assert_that(result.no_files).is_true()
    assert_that(result.skipped).is_false()
    assert_that(result.issues_count).is_zero()
    assert_that(result.issues).is_empty()
    assert_that(result.output).is_equal_to(output)
    assert_that((batch_project / "app.ts").read_text()).is_equal_to("debugger;\n")


@pytest.mark.parametrize("action", ["check", "fix"])
@pytest.mark.parametrize(
    ("plugin_cls", "returncode", "stdout", "stderr", "parse_failures"),
    [
        (OxlintPlugin, 2, OXLINT_NO_FILES, "", 0),
        (OxlintPlugin, -11, OXLINT_NO_FILES, "", 0),
        (OxlintPlugin, 1, OXLINT_NO_FILES, "error: invalid configuration\n", 0),
        (OxlintPlugin, 1, "", OXLINT_NO_FILES, 0),
        (OxlintPlugin, 1, OXLINT_NO_FILES + "error: invalid configuration\n", "", 0),
        (OxlintPlugin, 1, OXLINT_NO_FILES + "{}", "", 1),
        (
            OxlintPlugin,
            1,
            OXLINT_NO_FILES.replace('"number_of_files": 0', '"number_of_files": 1'),
            "",
            0,
        ),
        (
            OxlintPlugin,
            1,
            OXLINT_NO_FILES.replace('"number_of_files": 0', '"number_of_files": false'),
            "",
            0,
        ),
        (
            OxlintPlugin,
            1,
            OXLINT_NO_FILES.replace('"number_of_files": 0', '"number_of_files": "0"'),
            "",
            0,
        ),
        (
            OxlintPlugin,
            1,
            OXLINT_NO_FILES.replace('"number_of_files": 0', '"other": 0'),
            "",
            0,
        ),
        (
            OxlintPlugin,
            1,
            OXLINT_NO_FILES.replace('"diagnostics": []', '"diagnostics": null'),
            "",
            1,
        ),
        (
            OxlintPlugin,
            1,
            OXLINT_NO_FILES.replace('"diagnostics": []', '"diagnostics": [null]'),
            "",
            1,
        ),
        (
            OxlintPlugin,
            1,
            OXLINT_NO_FILES.replace('"diagnostics": []', '"other": []'),
            "",
            1,
        ),
        (
            OxlintPlugin,
            1,
            OXLINT_NO_FILES.replace('"diagnostics": []', '"diagnostics": ['),
            "",
            1,
        ),
        (
            OxlintPlugin,
            1,
            OXLINT_NO_FILES.replace("No files found", "No files were found"),
            "",
            0,
        ),
        (OxlintPlugin, 1, OXLINT_NO_FILES.split("\n", 1)[1], "", 0),
        (OxlintPlugin, 1, OXLINT_NO_FILES.split("\n", 1)[0], "", 0),
        (OxlintPlugin, 1, OXLINT_NO_FILES.split("\n", 1)[0] + "\n[]", "", 1),
        (
            OxlintPlugin,
            1,
            OXLINT_NO_FILES.replace('"number_of_files": 0', '"number_of_files": 0.0'),
            "",
            0,
        ),
        (OxfmtPlugin, 1, "", OXFMT_NO_FILES, None),
        (OxfmtPlugin, 137, "", OXFMT_NO_FILES, None),
        (OxfmtPlugin, 2, "error: config.js\n", OXFMT_NO_FILES, None),
        (OxfmtPlugin, 2, "", "error: " + OXFMT_NO_FILES, None),
        (OxfmtPlugin, 2, "", OXFMT_NO_FILES + "error: config.js\n", None),
        (OxfmtPlugin, 2, "", "Expected at least one target file\n", None),
        (OxfmtPlugin, 2, "", OXFMT_NO_FILES.replace("rules.", "rules!"), None),
        (OxfmtPlugin, 2, OXFMT_NO_FILES, OXFMT_NO_FILES, None),
    ],
)
def test_no_target_lookalikes_still_fail_closed(
    action: str,
    plugin_cls: type[BaseToolPlugin],
    returncode: int,
    stdout: str,
    stderr: str,
    parse_failures: int | None,
    batch_project: Path,
) -> None:
    """Near matches, wrong statuses and extra failures cannot become skips.

    Args:
        action: Check or fix entry point.
        plugin_cls: Tool whose output recognition is exercised.
        returncode: Failed process status.
        stdout: Malformed or lookalike stdout.
        stderr: Malformed or lookalike stderr.
        parse_failures: Expected number of rejected Oxlint reports or records.
        batch_project: Discovered target directory.
    """
    plugin = plugin_cls()
    output = stdout + stderr
    with patch.object(
        plugin,
        "_run_subprocess_result",
        side_effect=[SubprocessResult(returncode, stdout, stderr, output)],
    ):
        result = getattr(plugin, action)([str(batch_project)], {})

    assert_that(result.success).is_false()
    assert_that(result.no_files).is_false()
    assert_that(result.skipped).is_false()
    assert_that(result.parse_failures_count).is_equal_to(parse_failures)
    if parse_failures:
        if action == "fix":
            assert_that(result.residual_unknown).is_true()
            assert_that(result.fixed_issues_count).is_none()
            assert_that(result.remaining_issues_count).is_none()
            output = output.rstrip()
        output = (
            f"Failed to parse {parse_failures} Oxlint report(s) or diagnostic(s).\n"
            f"{output}"
        )
    assert_that(result.output).is_equal_to(output)


@pytest.mark.parametrize(
    ("plugin_cls", "stdout", "stderr"),
    [
        (OxlintPlugin, OXLINT_FINDINGS, "warning: config contains }\n"),
        (OxfmtPlugin, "app.ts\n", "Failed to load config.js\n"),
        (PrettierPlugin, "Checking formatting...\n", "[warn] app.css\n"),
    ],
)
def test_findings_use_the_tools_report_stream(
    plugin_cls: type[BaseToolPlugin],
    stdout: str,
    stderr: str,
    batch_project: Path,
) -> None:
    """JSON/path reports exclude stderr; Prettier warnings still use stderr.

    Args:
        plugin_cls: Tool whose parser uses the streams.
        stdout: Report or progress output.
        stderr: Warning or error output.
        batch_project: Discovered target directory.
    """
    plugin = plugin_cls()
    with patch.object(
        plugin,
        "_run_subprocess_result",
        return_value=SubprocessResult(1, stdout, stderr, stdout + stderr),
    ):
        result = plugin.check([str(batch_project)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(len(result.issues or [])).is_equal_to(1)
    assert_that(result.output).is_equal_to(stdout + stderr)


@pytest.mark.parametrize("failed_stage", [0, 1, 2], ids=["precheck", "write", "verify"])
@pytest.mark.parametrize(
    "verify_status",
    [VerifyStatus.UNCHANGED, VerifyStatus.VERIFIED],
)
def test_failed_writes_keep_unknown_accounting_until_measured(
    failed_stage: int,
    verify_status: VerifyStatus,
    batch_project: Path,
) -> None:
    """No retry or empty verify scope may turn unknown counts into JSON zeroes.

    Args:
        failed_stage: Stage returning an execution failure.
        verify_status: Whether a real run-level check measured an after-state.
        batch_project: Discovered target directory.
    """
    plugin = OxlintPlugin()
    processes = [
        SubprocessResult(1, OXLINT_FINDINGS, "", OXLINT_FINDINGS),
        SubprocessResult(0, "", "", ""),
        SubprocessResult(0, "", "", ""),
    ][: failed_stage + 1]
    processes[failed_stage] = SubprocessResult(2, "", "crashed\n", "crashed\n")
    with patch.object(plugin, "_run_subprocess_result", side_effect=processes):
        result = run_fix_with_retry(plugin, [str(batch_project)], {}, max_retries=3)

    assert_that(result.residual_unknown).is_true()
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
    result.capability = Cap.FIX
    fold_verify_results(
        mutation_results=[result],
        verify_results=[
            VerifyOutcome(
                tool="oxlint",
                status=verify_status,
                result=(
                    ToolResult(name="oxlint", success=True, issues=[])
                    if verify_status is VerifyStatus.VERIFIED
                    else None
                ),
            ),
        ],
        scope=VerifyScope(
            files=(str(batch_project / "app.ts"),),
            narrowed=True,
        ),
    )
    payload = serialize_tool_result(result, action=Action.FIX)
    assert_that(result.success).is_false()
    assert_that(result.output).contains("crashed")
    measured = verify_status is VerifyStatus.VERIFIED
    assert_that(result.residual_unknown).is_equal_to(not measured)
    assert_that(payload["net_resolved"]).is_equal_to(
        int(failed_stage > 0) if measured else None,
    )
    assert_that(payload["remaining"]).is_equal_to(0 if measured else None)


@pytest.mark.parametrize("failed_stage", [1, 2], ids=["write", "verify"])
@pytest.mark.parametrize(
    ("plugin_cls", "findings", "no_files_process"),
    [
        (
            OxlintPlugin,
            OXLINT_FINDINGS,
            SubprocessResult(1, OXLINT_NO_FILES, "", OXLINT_NO_FILES),
        ),
        (
            OxfmtPlugin,
            "app.ts\n",
            SubprocessResult(2, "", OXFMT_NO_FILES, OXFMT_NO_FILES),
        ),
    ],
)
def test_later_no_files_does_not_erase_initial_findings(
    failed_stage: int,
    plugin_cls: type[BaseToolPlugin],
    findings: str,
    no_files_process: SubprocessResult,
    batch_project: Path,
) -> None:
    """A disappeared target after checking cannot become a clean fix result.

    Args:
        failed_stage: Stage at which all targets disappear.
        plugin_cls: Tool running the fix pipeline.
        findings: Initial check report.
        no_files_process: Captured no-target process outcome.
        batch_project: Discovered target directory.
    """
    plugin = plugin_cls()
    processes = [
        SubprocessResult(1, findings, "", findings),
        SubprocessResult(0, "", "", ""),
        SubprocessResult(0, "", "", ""),
    ][: failed_stage + 1]
    processes[failed_stage] = no_files_process
    with patch.object(plugin, "_run_subprocess_result", side_effect=processes):
        result = plugin.fix([str(batch_project)], {})

    assert_that(result.success).is_false()
    assert_that(result.no_files).is_false()
    assert_that(result.residual_unknown).is_true()
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.remaining_issues_count).is_none()
    assert_that(result.output).is_equal_to(no_files_process.output)


@pytest.mark.parametrize(
    ("plugin_cls", "write_output"),
    [(OxfmtPlugin, "config.js\n"), (PrettierPlugin, "[warn] config.css\n")],
)
def test_formatter_write_output_cannot_validate_a_failed_write(
    plugin_cls: type[BaseToolPlugin],
    write_output: str,
    batch_project: Path,
) -> None:
    """The write command's progress cannot be parsed as a check finding.

    Args:
        plugin_cls: Formatter that does not emit a check report during writes.
        write_output: Progress text resembling a check finding.
        batch_project: Discovered target directory.
    """
    plugin = plugin_cls()
    with patch.object(
        plugin,
        "_run_subprocess_result",
        side_effect=[
            SubprocessResult(0, "", "", ""),
            SubprocessResult(1, write_output, "", write_output),
        ],
    ):
        result = plugin.fix([str(batch_project)], {})

    assert_that(result.success).is_false()
    assert_that(result.residual_unknown).is_true()
    assert_that(result.issues).is_empty()
    assert_that(result.remaining_issues_count).is_none()
    assert_that(result.output).is_equal_to(write_output)


def test_a_failed_retry_keeps_the_first_before_state(batch_project: Path) -> None:
    """An interrupted retry cannot replace the original findings or claim fixes.

    Args:
        batch_project: Discovered target directory.
    """
    data = json.loads(OXLINT_FINDINGS)
    diagnostic = data["diagnostics"][0]
    data["diagnostics"] = [
        {**diagnostic, "filename": filename}
        for filename in ("app.ts", "second.ts", "third.ts")
    ]
    first_report = json.dumps(data)
    plugin = OxlintPlugin()
    with patch.object(
        plugin,
        "_run_subprocess_result",
        side_effect=[
            SubprocessResult(1, first_report, "", first_report),
            SubprocessResult(0, "", "", ""),
            SubprocessResult(1, OXLINT_FINDINGS, "", OXLINT_FINDINGS),
            SubprocessResult(1, OXLINT_FINDINGS, "", OXLINT_FINDINGS),
            SubprocessResult(2, "", "retry crashed\n", "retry crashed\n"),
        ],
    ):
        result = run_fix_with_retry(plugin, [str(batch_project)], {}, max_retries=3)

    assert_that(result.success).is_false()
    assert_that(result.initial_issues_count).is_equal_to(3)
    assert_that(len(result.initial_issues or [])).is_equal_to(3)
    assert_that(result.issues_count).is_equal_to(3)
    assert_that(result.issues).is_equal_to(result.initial_issues)
    assert_that(result.residual_unknown).is_true()
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
    assert_that(result.output).is_equal_to("retry crashed\n")


def test_oxlint_fix_keeps_json_findings_with_stderr_braces(batch_project: Path) -> None:
    """Fix-stage JSON stays parseable and its verbose label names the format.

    Args:
        batch_project: Discovered target directory.
    """
    plugin = OxlintPlugin()
    warning = "warning: config contains }\n"
    process = SubprocessResult(1, OXLINT_FINDINGS, warning, OXLINT_FINDINGS + warning)
    with patch.object(plugin, "_run_subprocess_result", side_effect=[process] * 3):
        result = plugin.fix([str(batch_project)], {"verbose_fix_output": True})

    assert_that(result.success).is_false()
    assert_that(result.residual_unknown).is_false()
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.remaining_issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_zero()
    assert_that(result.output).contains(
        "Fix output (oxlint JSON)",
        OXLINT_FINDINGS,
        warning,
    )


@pytest.mark.parametrize("action", ["check", "fix"])
def test_zero_files_with_diagnostics_is_not_a_no_files_result(
    action: str,
    batch_project: Path,
) -> None:
    """Contradictory counters cannot hide actual diagnostics as no-targets.

    Args:
        action: Check or fix entry point.
        batch_project: Discovered target directory.
    """
    output = (
        OXLINT_NO_FILES.split("\n", 1)[0]
        + "\n"
        + OXLINT_FINDINGS.replace('"number_of_files": 1', '"number_of_files": 0')
    )
    plugin = OxlintPlugin()
    with patch.object(
        plugin,
        "_run_subprocess_result",
        return_value=SubprocessResult(1, output, "", output),
    ):
        result = getattr(plugin, action)(
            [str(batch_project)],
            {"verbose_fix_output": True},
        )

    assert_that(result.success).is_false()
    assert_that(result.no_files).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.output).contains(output)
