"""Security scanners with findings stay baselineable (#2400).

Bandit and Semgrep fail the check when a complete scan has findings, as most
tools do. The failed verdict is explained by those findings only if the
producer certifies them as complete, so these tests build each result with the
real plugin (only the subprocess is doubled) and push it through the baseline
create command and the baseline filter.
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 - canned process results only; nothing is executed
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli
from lintro.models.core.run_artifact import RunArtifact
from lintro.models.core.tool_result import ToolResult
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.bandit.definition import BanditPlugin
from lintro.tools.semgrep.definition import SemgrepPlugin
from lintro.utils.execution.baseline_filter import apply_baseline
from lintro.utils.fingerprint_baseline import baseline_incomplete_reason, snapshot

_SOURCE = "import subprocess\n\n\ndef f(x):\n    eval(x)\n    subprocess.call(x, shell=True)\n"


def _bandit_result(target: Path) -> ToolResult:
    """Run the real Bandit plugin against a canned two-finding report.

    Args:
        target: Python file the report names.

    Returns:
        The plugin's ToolResult.
    """
    finding = {
        "filename": str(target),
        "line_number": 5,
        "col_offset": 4,
        "issue_severity": "MEDIUM",
        "issue_confidence": "HIGH",
        "test_id": "B307",
        "test_name": "blacklist",
        "issue_text": "Use of possibly insecure function - consider ast.literal_eval.",
    }
    shell = {
        **finding,
        "line_number": 6,
        "issue_severity": "HIGH",
        "test_id": "B602",
        "test_name": "subprocess_popen_with_shell_equals_true",
        "issue_text": "subprocess call with shell=True identified.",
    }
    report = {
        "errors": [],
        "results": [finding, shell],
        "metrics": {str(target): {"loc": 5}, "_totals": {"loc": 5}},
    }
    completed = subprocess.CompletedProcess(
        args=["bandit"],
        returncode=1,
        stdout=json.dumps(report),
        stderr="",
    )
    with patch("lintro.tools.bandit.definition.subprocess.run", return_value=completed):
        return BanditPlugin().check([str(target)], {})


def _semgrep_result(target: Path) -> ToolResult:
    """Run the real Semgrep plugin against a canned two-finding report.

    Semgrep exits 0 with findings because lintro does not pass ``--error``.

    Args:
        target: Python file the report names.

    Returns:
        The plugin's ToolResult.
    """
    results = [
        {
            "check_id": rule,
            "path": str(target),
            "start": {"line": line, "col": 5},
            "end": {"line": line, "col": 12},
            "extra": {"message": message, "severity": severity},
        }
        for rule, line, message, severity in (
            ("no-eval", 5, "eval used", "WARNING"),
            ("no-shell", 6, "shell true", "ERROR"),
        )
    ]
    report = json.dumps(
        {"results": results, "errors": [], "paths": {"scanned": [str(target)]}},
    )
    plugin = SemgrepPlugin()
    with patch.object(
        plugin,
        "_run_subprocess_result",
        return_value=SubprocessResult(0, report, "", report),
    ):
        return plugin.check([str(target)], {})


_PRODUCERS: dict[str, Callable[[Path], ToolResult]] = {
    "bandit": _bandit_result,
    "semgrep": _semgrep_result,
}


@pytest.fixture(params=sorted(_PRODUCERS))
def scanner_result(request: pytest.FixtureRequest, workspace: Path) -> ToolResult:
    """Return a real scanner result with two findings in the workspace.

    Args:
        request: Selects the producer.
        workspace: Invocation root.

    Returns:
        The producer's ToolResult.
    """
    target = workspace / "app.py"
    target.write_text(_SOURCE, encoding="utf-8")
    return _PRODUCERS[request.param](target)


def test_findings_fail_the_check_but_certify_completeness(
    scanner_result: ToolResult,
) -> None:
    """A complete scan with findings is a failed, complete, baselineable verdict."""
    assert_that(scanner_result.issues_count).is_equal_to(2)
    assert_that(scanner_result.success).is_false()
    assert_that(scanner_result.execution_failed).is_false()
    assert_that(scanner_result.findings_complete).is_true()
    assert_that(baseline_incomplete_reason(scanner_result)).is_none()


def test_baseline_create_writes_every_finding(
    scanner_result: ToolResult,
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``lintro baseline create`` acknowledges both findings."""
    monkeypatch.setattr(
        "lintro.cli_utils.commands.baseline.execute_run",
        lambda **_: RunArtifact(
            workspace_root=workspace,
            tool_results=[scanner_result],
        ),
    )

    result = CliRunner().invoke(
        cli,
        ["baseline", "create", "--tools", scanner_result.name],
    )

    assert_that(result.exit_code).described_as(result.output).is_zero()
    written = json.loads((workspace / ".lintro-baseline.json").read_text())
    assert_that(
        sorted((entry["tool"], entry["file"]) for entry in written["entries"]),
    ).is_equal_to([(scanner_result.name, "app.py")] * 2)


def test_baseline_filter_suppresses_acknowledged_findings(
    scanner_result: ToolResult,
    workspace: Path,
) -> None:
    """An existing baseline turns the run green with nothing left to report."""
    baseline = snapshot([scanner_result], root=workspace)
    artifact = RunArtifact(
        tool_results=[scanner_result],
        workspace_root=workspace,
        total_issues=2,
        exit_code=1,
    )

    apply_baseline(artifact, baseline)

    filtered = artifact.tool_results[0]
    assert_that(filtered.issues).is_empty()
    assert_that(filtered.baselined_count).is_equal_to(2)
    assert_that(filtered.success).is_true()
    assert_that(artifact.exit_code).is_zero()
