"""Acknowledgements cannot erase incomplete producer verdicts."""

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from lintro.models.core.run_artifact import RunArtifact
from lintro.models.core.tool_result import ToolResult
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.black.definition import BlackPlugin
from lintro.tools.core.verify_pass import (
    VerifyOutcome,
    VerifyScope,
    fold_verify_results,
)
from lintro.tools.ruff.definition import RuffPlugin
from lintro.tools.shellcheck.definition import ShellcheckPlugin
from lintro.utils.execution.baseline_filter import apply_baseline
from lintro.utils.fingerprint_baseline import maintain_baseline, snapshot
from tests.unit.tools.subprocess_helpers import subprocess_result


def _artifact(result: ToolResult, root: Path) -> RunArtifact:
    """Represent the failing check before acknowledgements are applied."""
    return RunArtifact(
        tool_results=[result],
        workspace_root=root,
        total_issues=result.issues_count,
        exit_code=1,
    )


@pytest.mark.parametrize("syntax_error", [False, True])
def test_real_black_mixed_verdict_keeps_native_error(
    workspace: Path,
    syntax_error: bool,
) -> None:
    """Real Black's exit 123 cannot be hidden by an acknowledged format finding."""
    dirty = workspace / "a.py"
    dirty.write_text("x=1\n", encoding="utf-8")
    plugin = BlackPlugin()
    baseline = snapshot([plugin.check([str(dirty)], {})], root=workspace)
    assert len(baseline.entries) == 1
    files = [str(dirty)]
    if syntax_error:
        broken = workspace / "b.py"
        broken.write_text("def f(:\n", encoding="utf-8")
        files.append(str(broken))
    result = plugin.check(files, {})
    assert result.issues_count == 1
    artifact = _artifact(result, workspace)

    apply_baseline(artifact, baseline)

    assert artifact.exit_code == int(syntax_error)
    assert result.success is not syntax_error
    if syntax_error:
        assert result.output and "cannot parse" in result.output.lower()
        assert result.execution_failed
        assert result.baselined_count == 0
    else:
        assert result.baselined_count == 1


@pytest.mark.parametrize("failure", [None, "", "shellcheck: internal error, aborting"])
def test_per_file_failure_survives_acknowledged_peer(
    workspace: Path,
    failure: str | None,
) -> None:
    """A silent nonzero file and a dirty peer remain an execution failure."""
    files = [workspace / name for name in ("a.sh", "b.sh")]
    for file in files:
        file.write_text("#!/bin/sh\necho $X\n", encoding="utf-8")
    output = json.dumps(
        [
            {
                "file": str(files[1]),
                "line": 2,
                "column": 6,
                "code": 2086,
                "level": "warning",
                "message": "Double quote",
            },
        ],
    )
    plugin = ShellcheckPlugin()
    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            plugin,
            "_run_subprocess_result",
            return_value=subprocess_result(False, output),
        ),
    ):
        baseline = snapshot([plugin.check([str(files[1])], {})], root=workspace)
    assert len(baseline.entries) == 1
    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            plugin,
            "_run_subprocess_result",
            side_effect=[
                (
                    subprocess_result(True, "[]")
                    if failure is None
                    else subprocess_result(False, failure)
                ),
                subprocess_result(False, output),
            ],
        ),
    ):
        result = plugin.check([str(file) for file in files], {})
    artifact = _artifact(result, workspace)

    apply_baseline(artifact, baseline)

    assert artifact.exit_code == int(failure is not None)
    assert result.execution_failed is (failure is not None)
    if failure is not None:
        assert result.output
        assert failure in result.output
        assert result.baselined_count == 0
    else:
        assert result.baselined_count == 1


def test_findings_alone_cannot_certify_a_failed_producer(
    tmp_path: Path,
    dirty_result: ToolResult,
) -> None:
    """Legacy inspection and parsed findings do not explain a failing status."""
    baseline = snapshot([dirty_result], root=tmp_path)
    result = ToolResult(
        name=dirty_result.name,
        success=False,
        inspected_files=True,
        issues=dirty_result.issues,
        issues_count=dirty_result.issues_count,
        output="unclassified execution error",
    )
    artifact = _artifact(result, tmp_path)

    apply_baseline(artifact, baseline)

    assert artifact.exit_code == 1
    assert result.output == "unclassified execution error"
    assert result.baselined_count == 0


@pytest.mark.parametrize("stage", ["lint", "format"])
def test_ruff_native_error_cannot_authorize_filtering_or_pruning(
    workspace: Path,
    stage: str,
) -> None:
    """Ruff may return a finding and an error in the same native report."""
    file = workspace / "a.py"
    file.write_text("import os\n", encoding="utf-8")
    plugin = RuffPlugin()
    plugin.set_options(format_check=stage == "format", _baseline_coverage=True)
    baseline = snapshot([plugin.check([str(file)], {})], root=workspace)
    assert baseline.entries
    output = json.dumps(
        [
            {
                "filename": str(file),
                "location": {"row": 1, "column": 1},
                "code": "F401",
                "message": "`os` imported but unused",
            },
        ],
    )
    error = "error: failed to read b.py: Permission denied"
    reports = [SubprocessResult(2, output, error, output + "\n" + error)]
    if stage == "format":
        reports = [
            SubprocessResult(1, output, "", output),
            SubprocessResult(
                2,
                "Would reformat: a.py\n",
                error,
                "Would reformat: a.py\n" + error,
            ),
        ]
    reports.append(SubprocessResult(0, str(file), "", str(file)))
    with patch.object(plugin, "_run_subprocess_result", side_effect=reports):
        result = plugin.check([str(file)], {})
    artifact = _artifact(result, workspace)
    previous = baseline.model_copy(
        update={
            "entries": [
                *baseline.entries,
                baseline.entries[0].model_copy(update={"code": "F999"}),
            ],
        },
    )

    maintained = maintain_baseline(
        previous,
        snapshot([result], root=workspace),
        results=[result],
        root=workspace,
        prune=True,
    )
    apply_baseline(artifact, baseline)

    assert maintained.entries == previous.entries
    assert artifact.exit_code == 1
    assert result.output and error in result.output
    assert result.inspected_paths is None
    assert result.execution_failed


def test_black_supplement_failure_is_not_explained_by_format_findings(
    workspace: Path,
) -> None:
    """Black's separate E501 measurement must finish before filtering a verdict."""
    file = workspace / "a.py"
    file.write_text("x=1\n", encoding="utf-8")
    plugin = BlackPlugin()
    baseline = snapshot([plugin.check([str(file)], {})], root=workspace)
    from lintro.tools.core.line_length_checker import LineLengthCheckUnavailableError

    with patch.object(
        plugin,
        "_check_line_length_violations",
        side_effect=LineLengthCheckUnavailableError("invalid report"),
    ):
        result = plugin.check([str(file)], {})
    artifact = _artifact(result, workspace)
    apply_baseline(artifact, baseline)
    assert artifact.exit_code == 1
    assert result.output and "invalid report" in result.output


@pytest.mark.parametrize(
    "returncode,summary",
    [(123, ""), (1, "2 files would be reformatted.")],
)
def test_black_requires_native_verdict_and_complete_parsing(
    workspace: Path,
    returncode: int,
    summary: str,
) -> None:
    """A native error without error text or an incomplete parse stays failing."""
    file = workspace / "a.py"
    file.write_text("x=1\n", encoding="utf-8")
    plugin = BlackPlugin()
    baseline = snapshot([plugin.check([str(file)], {})], root=workspace)
    output = f"would reformat a.py\n{summary}\n"
    with (
        patch.object(
            plugin,
            "_run_subprocess_result",
            return_value=SubprocessResult(returncode, "", output, output),
        ),
        patch.object(plugin, "_check_line_length_violations", return_value=[]),
    ):
        result = plugin.check([str(file)], {})
    artifact = _artifact(result, workspace)

    apply_baseline(artifact, baseline)

    assert artifact.exit_code == 1
    assert result.output == output
    assert result.baselined_count == 0
    assert result.execution_failed is (returncode == 123)
    assert result.parse_failures_count == int(returncode == 1)


@pytest.mark.parametrize("suffix", [", {}]", "] trailing error"])
def test_ruff_partial_report_cannot_certify_coverage(
    workspace: Path,
    suffix: str,
) -> None:
    """A tolerant parser's recovered finding cannot certify the entire report."""
    file = workspace / "a.py"
    file.write_text("import os\n", encoding="utf-8")
    plugin = RuffPlugin()
    plugin.set_options(format_check=False, _baseline_coverage=True)
    baseline = snapshot([plugin.check([str(file)], {})], root=workspace)
    issue = {
        "filename": str(file),
        "location": {"row": 1, "column": 1},
        "code": "F401",
        "message": "`os` imported but unused",
    }
    output = json.dumps([issue])[:-1] + suffix
    with patch.object(
        plugin,
        "_run_subprocess_result",
        return_value=SubprocessResult(1, output, "", output),
    ):
        result = plugin.check([str(file)], {})
    artifact = _artifact(result, workspace)

    apply_baseline(artifact, baseline)

    assert artifact.exit_code == 1
    assert result.output == output
    assert result.parse_failures_count
    assert result.inspected_paths is None


@pytest.mark.parametrize("verdict", ["complete", "unknown", "execution_failed"])
def test_verify_preserves_completeness_proof_and_error_output(
    tmp_path: Path,
    dirty_result: ToolResult,
    verdict: str,
) -> None:
    """A final verify error survives folding and acknowledgement of its findings."""
    baseline = snapshot([dirty_result], root=tmp_path)
    mutation = replace(
        dirty_result,
        success=True,
        issues=[],
        issues_count=0,
        initial_issues=dirty_result.issues,
        initial_issues_count=1,
        fixed_issues_count=1,
        remaining_issues_count=0,
    )
    verified = replace(
        dirty_result,
        findings_complete=verdict == "complete",
        execution_failed=verdict == "execution_failed",
        output="native error" if verdict != "complete" else "lint finding",
    )
    results = [mutation]
    fold_verify_results(
        mutation_results=results,
        verify_results=[VerifyOutcome(tool="ruff", result=verified)],
        scope=VerifyScope(files=(str(tmp_path / "src/a.py"),), narrowed=True),
    )
    artifact = _artifact(results[0], tmp_path)

    apply_baseline(artifact, baseline)

    assert artifact.exit_code == int(verdict != "complete")
    assert results[0].baselined_count == int(verdict == "complete")
    if verdict != "complete":
        assert results[0].output and "native error" in results[0].output
