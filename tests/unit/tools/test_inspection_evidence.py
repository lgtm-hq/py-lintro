"""Inspection coverage needs file evidence, not merely an attempted command."""

from __future__ import annotations

import json
import subprocess  # nosec B404 - mocked process results only
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli_utils.commands.badge import badge_command
from lintro.enums.action import Action
from lintro.models.core.run_artifact import RunArtifact
from lintro.models.core.tool_result import ToolResult
from lintro.plugins.registry import ToolRegistry
from lintro.tools.golangci_lint.definition import _merge_fix_results
from lintro.utils.meaningful_run import baseline_is_eligible
from lintro.utils.severity_counts import count_severities

_FINDINGS = {
    "actionlint": ".github/workflows/ci.yml:1:1: unexpected key [syntax-check]",
    "shellcheck": json.dumps(
        [
            {
                "file": "test.sh",
                "line": 2,
                "column": 6,
                "level": "warning",
                "code": 2086,
                "message": "Quote this",
            },
        ],
    ),
    "clippy": json.dumps(
        {
            "reason": "compiler-message",
            "message": {
                "code": {"code": "clippy::needless_return"},
                "level": "warning",
                "message": "unneeded return",
                "spans": [{"file_name": "src/main.rs", "line_start": 1}],
            },
        },
    ),
    "golangci_lint": json.dumps(
        {
            "Issues": [
                {
                    "FromLinter": "errcheck",
                    "Text": "Unchecked error",
                    "Pos": {"Filename": "main.go", "Line": 1},
                },
            ],
        },
    ),
    "tsc": "main.ts(1,1): error TS2322: Type mismatch",
}


def _project(root: Path, tool: str) -> None:
    """Write only the files needed by the real plugin's discovery path.

    Args:
        root: Isolated project directory.
        tool: Registered tool name.
    """
    layouts = {
        "actionlint": {".github/workflows/ci.yml": "name: CI\non: push\njobs: {}\n"},
        "shellcheck": {"test.sh": "#!/bin/sh\necho ok\n"},
        "shfmt": {"test.sh": "#!/bin/sh\necho ok\n"},
        "pydoclint": {"main.py": '"""Clean source."""\n'},
        "clippy": {
            "Cargo.toml": '[package]\nname = "demo"\nversion = "0.1.0"\n',
            "src/main.rs": "fn main() {}\n",
        },
        "golangci_lint": {
            "go.mod": "module example.com/demo\n",
            "main.go": "package main\n",
        },
        "html_validate": {"index.html": "<p>ok</p>\n"},
        "tsc": {"main.ts": "export const x = 1;\n"},
    }
    for relative, content in layouts[tool].items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


@pytest.mark.parametrize(
    ("tool", "action"),
    [
        (tool, action)
        for tool in (
            "actionlint",
            "shellcheck",
            "shfmt",
            "pydoclint",
            "clippy",
            "html_validate",
            "golangci_lint",
            "tsc",
        )
        for action in (
            ("check", "fix")
            if tool in {"clippy", "golangci_lint", "shfmt"}
            else ("check",)
        )
    ],
)
@pytest.mark.parametrize("returncode", [0, 2])
def test_selected_files_count_as_inspected_only_after_successful_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    action: str,
    returncode: int,
) -> None:
    """A clean check qualifies for publication; an execution failure does not.

    Args:
        tmp_path: Isolated target directory.
        monkeypatch: Restores the working directory and discovery gates.
        tool: Plugin exercising its actual command and parser path.
        action: Check or fix for tools supporting it.
        returncode: Clean exit or a configuration/build failure.
    """
    plugin = type(ToolRegistry.get(tool))()
    _project(tmp_path, tool)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "lintro.plugins.execution_preparation.verify_tool_version",
        lambda *_, **__: None,
    )
    monkeypatch.setattr("shutil.which", lambda *_: "/mock/bin/tool")
    stdout = '{"Issues": []}' if tool == "golangci_lint" else ""
    completed = subprocess.CompletedProcess([tool], returncode, stdout, "")
    with patch("subprocess.run", return_value=completed):
        result = getattr(plugin, action)([str(tmp_path)], {})

    assert_that(result.skipped).is_false()
    assert_that(result.no_files).is_false()
    assert_that(result.inspected_files).is_equal_to(True if returncode == 0 else None)
    assert_that(result.issues_count).is_zero()
    results = [result, ToolResult(name="ruff", success=True, inspected_files=True)]
    assert_that(
        baseline_is_eligible(
            action=Action.CHECK,
            dry_run_preview=False,
            tool_results=results,
        ),
    ).is_equal_to(returncode == 0)
    artifact = RunArtifact(
        tool_results=[result],
        severity_counts=count_severities([result]),
    )
    with patch("lintro.cli_utils.commands.badge.api.check_run", return_value=artifact):
        badge = CliRunner().invoke(badge_command, ["--url"])
    assert_that(badge.exit_code).is_equal_to(0 if returncode == 0 else 1)
    assert_that("img.shields.io" in badge.output).is_equal_to(returncode == 0)


@pytest.mark.parametrize("action", ["check", "fix"])
@pytest.mark.parametrize("report", ["", " \n\t "])
def test_absent_go_report_cannot_qualify_for_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    report: str,
) -> None:
    """Exit zero without the requested JSON report cannot establish a baseline."""
    _project(tmp_path, "golangci_lint")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "lintro.plugins.execution_preparation.verify_tool_version",
        lambda *_, **__: None,
    )
    monkeypatch.setattr("shutil.which", lambda *_: "/mock/bin/tool")
    plugin = type(ToolRegistry.get("golangci_lint"))()
    with patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(["golangci-lint"], 0, report, ""),
    ):
        result = getattr(plugin, action)([str(tmp_path)], {})

    assert_that(result.success).is_false()
    assert_that(result.parse_failures_count).is_equal_to(1)
    assert_that(result.issues_count).is_zero()
    assert_that(
        baseline_is_eligible(
            action=Action.CHECK,
            dry_run_preview=False,
            tool_results=[
                result,
                ToolResult(name="ruff", success=True, inspected_files=True),
            ],
        ),
    ).is_false()


@pytest.mark.parametrize(
    "tool",
    ["actionlint", "shellcheck", "html_validate", "clippy"],
)
def test_launch_failures_do_not_produce_inspection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
) -> None:
    """Caught launch errors establish no work; uncaught ones still propagate.

    Args:
        tmp_path: Real project files for discovery.
        monkeypatch: Restores path and version gates.
        tool: Per-file or batch plugin under test.
    """
    _project(tmp_path, tool)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "lintro.plugins.execution_preparation.verify_tool_version",
        lambda *_, **__: None,
    )
    monkeypatch.setattr("shutil.which", lambda *_: "/mock/bin/tool")
    plugin = type(ToolRegistry.get(tool))()
    with patch("subprocess.run", side_effect=OSError("launch denied")):
        if tool in {"html_validate", "clippy"}:
            with pytest.raises(OSError, match="launch denied"):
                plugin.check([str(tmp_path)], {})
        else:
            result = plugin.check([str(tmp_path)], {})
            assert_that(result.success).is_false()
            assert_that(result.inspected_files).is_false()


@pytest.mark.parametrize(
    ("coverage", "expected"),
    [([False, False], False), ([None, False], None), ([True, None], True), ([], False)],
)
def test_module_fix_aggregation_preserves_child_coverage(
    coverage: list[bool | None],
    expected: bool | None,
) -> None:
    """Aggregating modules cannot invent coverage or erase affirmative evidence.

    Args:
        coverage: Explicit child inspection states.
        expected: Three-state union of those states.
    """
    result = _merge_fix_results(
        name="golangci_lint",
        results=[
            ToolResult(name="golangci_lint", inspected_files=value)
            for value in coverage
        ],
    )
    assert_that(result.inspected_files).is_equal_to(expected)


@pytest.mark.parametrize("tool", ["golangci_lint", "tsc"])
@pytest.mark.parametrize("succeeded", [False, True])
def test_project_aggregation_preserves_clean_inspection_or_unknown_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    succeeded: bool,
) -> None:
    """Project coverage accepts clean execution and rejects configuration errors.

    Args:
        tmp_path: Multi-project workspace root.
        monkeypatch: Restores working directory and version checks.
        tool: Go modules or TypeScript subprojects.
        succeeded: Whether each project check completed successfully.
    """
    for name in ("one", "two"):
        root = tmp_path / name
        root.mkdir()
        _project(root, tool)
        if tool == "tsc":
            (root / "tsconfig.json").write_text(json.dumps({"include": ["*.ts"]}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "lintro.plugins.execution_preparation.verify_tool_version",
        lambda *_, **__: None,
    )
    plugin = type(ToolRegistry.get(tool))()
    clean_output = '{"Issues": []}' if tool == "golangci_lint" else ""
    with patch.object(
        plugin,
        "_run_subprocess",
        return_value=(
            succeeded,
            clean_output if succeeded else "invalid configuration",
        ),
    ):
        checked = plugin.check([str(tmp_path)], {})
        assert_that(checked.success).is_equal_to(succeeded)
        assert_that(checked.inspected_files).is_equal_to(True if succeeded else None)
        if tool == "golangci_lint":
            fixed = plugin.fix([str(tmp_path)], {})
            assert_that(fixed.success).is_equal_to(succeeded)
            assert_that(fixed.inspected_files).is_equal_to(True if succeeded else None)


@pytest.mark.parametrize("tool", list(_FINDINGS))
def test_parsed_file_findings_establish_inspection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
) -> None:
    """Real parsers provide file evidence independently of exit status.

    Args:
        tmp_path: Target project directory.
        monkeypatch: Restores cwd and external discovery gates.
        tool: Plugin whose native report contains a located finding.
    """
    _project(tmp_path, tool)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "lintro.plugins.execution_preparation.verify_tool_version",
        lambda *_, **__: None,
    )
    monkeypatch.setattr("shutil.which", lambda *_: "/mock/bin/tool")
    plugin = type(ToolRegistry.get(tool))()
    with patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess([tool], 1, _FINDINGS[tool], ""),
    ):
        result = plugin.check([str(tmp_path)], {})
        assert_that(result.inspected_files).is_true()
        assert_that(result.issues).is_length(1)
        if tool in {"clippy", "golangci_lint"}:
            fixed = plugin.fix([str(tmp_path)], {})
            assert_that(fixed.inspected_files).is_true()
            assert_that(fixed.initial_issues_count).is_equal_to(1)


@pytest.mark.parametrize(
    "messages",
    [[], [{"line": 1, "message": "Invalid HTML", "ruleId": "test-rule"}]],
)
def test_html_file_reports_establish_clean_or_dirty_inspection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    messages: list[dict[str, object]],
) -> None:
    """Per-file JSON reports prove coverage even when their findings are empty.

    Args:
        tmp_path: HTML target directory.
        monkeypatch: Restores working directory and version gate.
        messages: Empty findings or a validation issue in a file report.
    """
    _project(tmp_path, "html_validate")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "lintro.plugins.execution_preparation.verify_tool_version",
        lambda *_, **__: None,
    )
    monkeypatch.setattr("shutil.which", lambda *_: "/mock/bin/tool")
    report = json.dumps([{"filePath": "index.html", "messages": messages}])
    with patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(["html-validate"], 1, report, ""),
    ):
        result = ToolRegistry.get("html_validate").check([str(tmp_path)], {})
    assert_that(result.inspected_files).is_true()
    assert_that(result.issues_count).is_equal_to(len(messages))


@pytest.mark.parametrize("tool", ["actionlint", "shellcheck"])
def test_file_aggregation_keeps_findings_beside_launch_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
) -> None:
    """A later launch failure cannot erase another file's proven inspection.

    Args:
        tmp_path: Workspace holding two selected files.
        monkeypatch: Restores discovery gates and cwd.
        tool: Bespoke or shared per-file runner.
    """
    _project(tmp_path, tool)
    first = next(tmp_path.rglob("*.yml" if tool == "actionlint" else "*.sh"))
    first.with_name("other" + first.suffix).write_text(first.read_text())
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "lintro.plugins.execution_preparation.verify_tool_version",
        lambda *_, **__: None,
    )
    completed = subprocess.CompletedProcess([tool], 1, _FINDINGS[tool], "")
    with patch("subprocess.run", side_effect=[completed, OSError("launch denied")]):
        result = type(ToolRegistry.get(tool))().check([str(tmp_path)], {})
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.inspected_files).is_true()


@pytest.mark.parametrize("outcome", ["launch-failure", "framework-skip", "mixed"])
def test_typescript_project_coverage_distinguishes_no_work_from_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    """Known non-execution stays false; parsed findings survive failed siblings.

    Args:
        tmp_path: Workspace containing two independently configured projects.
        monkeypatch: Restores cwd, version checking and framework detection.
        outcome: Failed launches, deliberate skips, or a mixed execution.
    """
    for name in ("one", "two"):
        root = tmp_path / name
        root.mkdir()
        _project(root, "tsc")
        (root / "tsconfig.json").write_text('{"include": ["*.ts"]}')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "lintro.plugins.execution_preparation.verify_tool_version",
        lambda *_, **__: None,
    )
    plugin = type(ToolRegistry.get("tsc"))()
    if outcome == "framework-skip":
        monkeypatch.setattr(
            plugin,
            "_detect_framework_project",
            lambda *_: ("Vue", "vue-tsc"),
        )
    effects = (
        [(False, _FINDINGS["tsc"]), OSError("launch denied")]
        if outcome == "mixed"
        else OSError("launch denied")
    )
    with patch.object(plugin, "_run_subprocess", side_effect=effects):
        result = plugin.check([str(tmp_path)], {})
    assert_that(result.inspected_files).is_equal_to(outcome == "mixed")
    assert_that(result.issues_count).is_equal_to(1 if outcome == "mixed" else 0)
    assert_that(result.success).is_equal_to(outcome == "framework-skip")


@pytest.mark.parametrize(
    "tool",
    ["actionlint", "shellcheck", "clippy", "html_validate", "golangci_lint", "tsc"],
)
def test_timeout_never_becomes_unknown_or_inspected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
) -> None:
    """Coverage adjustments must preserve the explicit timeout no-work state.

    Args:
        tmp_path: Target project for the real plugin path.
        monkeypatch: Restores cwd and external gates.
        tool: Plugin whose subprocess times out before producing a report.
    """
    _project(tmp_path, tool)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "lintro.plugins.execution_preparation.verify_tool_version",
        lambda *_, **__: None,
    )
    monkeypatch.setattr("shutil.which", lambda *_: "/mock/bin/tool")
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired([tool], 1)):
        result = type(ToolRegistry.get(tool))().check([str(tmp_path)], {})
    assert_that(result.timed_out).is_true()
    assert_that(result.success).is_false()
    assert_that(result.inspected_files).is_false()
