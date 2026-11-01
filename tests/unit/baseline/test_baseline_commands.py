"""CLI lifecycle with real Ruff and conservative maintenance scans."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli
from lintro.config.config_loader import (
    _convert_pyproject_to_config,
    build_config_from_dict,
    clear_config_cache,
)
from lintro.models.core.run_artifact import RunArtifact
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.ruff.ruff_issue import RuffIssue
from lintro.utils.fingerprint_baseline import snapshot
from lintro.utils.fingerprint_baseline_file import (
    change_baseline,
    read_baseline,
    resolve_baseline_path,
)


def _project(root: Path) -> Path:
    """Write a minimal native Ruff project with just the unused-import rule."""
    (root / "pyproject.toml").write_text(
        '[tool.ruff.lint]\nselect = ["F401"]\n'
        "[tool.lintro.ruff]\nformat_check = false\n",
        encoding="utf-8",
    )
    file = root / "example.py"
    file.write_text("import os\n", encoding="utf-8")
    return file


def test_real_cli_lifecycle_reports_only_new_findings(workspace: Path) -> None:
    """Create, shifted matching, new findings, update, show, and prune work together."""
    file = _project(workspace)
    runner = CliRunner()
    created = runner.invoke(cli, ["baseline", "create", "--tools", "ruff"])
    assert_that(created.exit_code).is_equal_to(0)
    baseline_path = workspace / ".lintro-baseline.json"
    assert_that(read_baseline(baseline_path).entries).is_length(1)
    file.write_text("\n\nimport os\n", encoding="utf-8")
    checked = runner.invoke(
        cli,
        [
            "chk",
            "--tools",
            "ruff",
            "--baseline",
            str(baseline_path),
            "--output-format",
            "json",
            "--yes",
        ],
    )
    assert_that(checked.exit_code).is_equal_to(0)
    report = json.loads(checked.stdout)
    assert_that(report["summary"]["total_issues"]).is_zero()
    assert_that(report["summary"]["total_baselined"]).is_equal_to(1)
    file.write_text("import os\nimport sys\n", encoding="utf-8")
    checked = runner.invoke(
        cli,
        [
            "chk",
            "--tools",
            "ruff",
            "--baseline",
            str(baseline_path),
            "--output-format",
            "json",
            "--yes",
            "--output",
            "report.json",
        ],
    )
    assert_that(checked.exit_code).is_equal_to(1)
    report = json.loads(checked.stdout)
    saved = json.loads((workspace / "report.json").read_text())
    assert_that(report["summary"]["total_issues"]).is_equal_to(1)
    assert_that(saved["results"]).is_equal_to(report["results"])
    assert_that(saved["summary"]["total_baselined"]).is_equal_to(1)
    updated = runner.invoke(cli, ["baseline", "update", "--tools", "ruff"])
    assert_that(updated.exit_code).is_equal_to(0)
    shown = runner.invoke(cli, ["baseline", "show"])
    assert_that(shown.output).contains(
        "ruff: 2 findings in 1 files",
        "Total: 2 findings",
    )
    file.write_text("import os\n", encoding="utf-8")
    reduced = runner.invoke(cli, ["baseline", "update", "--tools", "ruff"])
    assert_that(reduced.exit_code).is_zero()
    assert_that(read_baseline(baseline_path).entries).is_length(1)
    file.write_text("", encoding="utf-8")
    pruned = runner.invoke(cli, ["baseline", "prune", "--tools", "ruff"])
    assert_that(pruned.exit_code).is_equal_to(0)
    assert_that(read_baseline(baseline_path).entries).is_empty()


def test_native_exclusions_cannot_authorize_pruning(workspace: Path) -> None:
    """A clean Ruff exit on an excluded explicit file is not proof it was checked."""
    file = _project(workspace)
    runner = CliRunner()
    result = runner.invoke(cli, ["baseline", "create", "--tools", "ruff"])
    assert_that(result.exit_code).is_zero()
    baseline_path = workspace / ".lintro-baseline.json"
    before = baseline_path.read_bytes()
    (workspace / "pyproject.toml").write_text(
        '[tool.ruff]\nforce-exclude = true\nexclude = ["example.py"]\n'
        '[tool.ruff.lint]\nselect = ["F401"]\n',
        encoding="utf-8",
    )
    file.write_text("", encoding="utf-8")
    result = runner.invoke(cli, ["baseline", "prune", "--tools", "ruff"])
    assert_that(result.exit_code).is_zero()
    assert_that(baseline_path.read_bytes()).is_equal_to(before)


@pytest.mark.parametrize("mode", ["update", "prune"])
@pytest.mark.parametrize(
    "tool_options",
    ["ruff:select=E501", " RuFf :ignore=F401 "],
)
def test_rule_overrides_cannot_remove_existing_debt(
    workspace: Path,
    mode: str,
    tool_options: str,
) -> None:
    """Exact Ruff file discovery cannot prove a rule omitted by user overrides."""
    _project(workspace)
    runner = CliRunner()
    created = runner.invoke(cli, ["baseline", "create", "--tools", "ruff"])
    assert_that(created.exit_code).is_zero()
    path = workspace / ".lintro-baseline.json"
    before = path.read_bytes()

    narrowed = runner.invoke(
        cli,
        ["baseline", mode, "--tools", "ruff", "--tool-options", tool_options],
    )

    assert_that(narrowed.exit_code).is_equal_to(0)
    assert_that(path.read_bytes()).is_equal_to(before)


@pytest.mark.parametrize("mode", ["update", "prune"])
@pytest.mark.parametrize("overridden_tool", ["ruff", "mypy"])
def test_overrides_preserve_budgets_without_disabling_peer_coverage(
    workspace: Path,
    dirty_result: ToolResult,
    finding: RuffIssue,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    overridden_tool: str,
) -> None:
    """Overrides retain old counts; update can add debt and peers can still prune."""
    peer_name = "mypy" if overridden_tool == "ruff" else "ruff"
    previous = snapshot(
        [
            replace(
                dirty_result,
                name=overridden_tool,
                issues=[finding, finding, replace(finding, code="F999")],
                issues_count=3,
            ),
            replace(dirty_result, name=peer_name),
        ],
        root=workspace,
    )
    path = workspace / ".lintro-baseline.json"
    change_baseline(path, lambda _: previous, create=True)
    narrowed = replace(
        dirty_result,
        name=overridden_tool,
        issues=[finding, replace(finding, code="E501")],
        issues_count=2,
        inspected_paths=frozenset({finding.file}),
    )
    peer = ToolResult(
        name=peer_name,
        success=True,
        inspected_files=True,
        inspected_paths=frozenset({finding.file}),
    )
    monkeypatch.setattr(
        "lintro.cli_utils.commands.baseline.execute_run",
        lambda **_: RunArtifact(
            tool_results=[narrowed, peer],
            workspace_root=workspace,
        ),
    )

    result = CliRunner().invoke(
        cli,
        [
            "baseline",
            mode,
            "--tools",
            "ruff,mypy",
            "--tool-options",
            f" {overridden_tool.upper()} :select=F401|E501,"
            f"{overridden_tool}:ignore=F999",
        ],
    )

    assert_that(result.exit_code).is_equal_to(0)
    entries = read_baseline(path).entries
    expected = {("F401", 2), ("F999", 1)}
    if mode == "update":
        expected.add(("E501", 1))
    assert_that({(entry.code, entry.count) for entry in entries}).is_equal_to(expected)
    assert_that({entry.tool for entry in entries}).is_equal_to({overridden_tool})


def test_configured_baseline_and_cli_override_are_opt_in(workspace: Path) -> None:
    """Configured filtering preserves the separate raw severity history."""
    _project(workspace)
    runner = CliRunner()
    created = runner.invoke(cli, ["baseline", "create", "--tools", "ruff"])
    assert_that(created.exit_code).is_zero()
    config_path = workspace / ".lintro-config.yaml"
    config_path.write_text(
        config_path.read_text() + "baseline: .lintro-baseline.json\n",
        encoding="utf-8",
    )
    clear_config_cache()
    log_root = workspace / ".lintro"
    log_root.mkdir(exist_ok=True)
    history = log_root / "severity-baseline.json"
    history.write_text("raw history sentinel", encoding="utf-8")
    result = runner.invoke(cli, ["chk", "--tools", "ruff", "--yes"])
    assert_that(result.exit_code).is_zero()
    assert_that(result.output).contains("0 issues (1 baselined)")
    assert_that(history.read_text()).is_equal_to("raw history sentinel")
    invalid = runner.invoke(
        cli,
        [
            "chk",
            "--tools",
            "ruff",
            "--baseline",
            "missing.json",
            "--yes",
        ],
    )
    assert_that(invalid.exit_code).is_equal_to(1)
    assert_that(invalid.output).contains("Cannot load fingerprint baseline")
    assert_that(history.read_text()).is_equal_to("raw history sentinel")


@pytest.mark.parametrize(
    "state",
    [
        {"inspected_files": None},
        {"inspected_files": False},
        {"timed_out": True, "inspected_files": False},
        {"parse_failures_count": 1},
        {"success": False, "issues": [], "issues_count": 0},
    ],
)
def test_failed_management_scan_never_rewrites_file(
    workspace: Path,
    dirty_result: ToolResult,
    monkeypatch: pytest.MonkeyPatch,
    state: dict[str, Any],
) -> None:
    """Incomplete scans fail closed before persisting any new acknowledgement."""
    baseline = snapshot([dirty_result], root=workspace)
    path = workspace / ".lintro-baseline.json"
    change_baseline(path, lambda _: baseline, create=True)
    before = path.read_bytes()
    broken = replace(dirty_result, **state)
    monkeypatch.setattr(
        "lintro.cli_utils.commands.baseline.execute_run",
        lambda **_: RunArtifact(tool_results=[broken], workspace_root=workspace),
    )
    result = CliRunner().invoke(cli, ["baseline", "update", "--tools", "ruff"])
    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.output).contains("Baseline unchanged")
    assert_that(path.read_bytes()).is_equal_to(before)


def test_create_and_show_validate_files_before_any_scan(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing or corrupt files cannot be replaced or shown as valid snapshots."""
    path = workspace / ".lintro-baseline.json"
    path.write_text('{"version": 99}', encoding="utf-8")

    def forbidden_scan(*args: object, **kwargs: object) -> None:
        pytest.fail("invalid baseline must fail before execution")

    monkeypatch.setattr("lintro.cli_utils.commands.baseline._scan", forbidden_scan)
    runner = CliRunner()
    for mode in ("create", "update", "prune", "show"):
        result = runner.invoke(cli, ["baseline", mode])
        assert_that(result.exit_code).is_equal_to(1)
    assert_that(path.read_text()).is_equal_to('{"version": 99}')


def test_yaml_and_toml_config_resolve_relative_to_config_file(workspace: Path) -> None:
    """The config schema retains the path in both supported project formats."""
    config = build_config_from_dict(
        {"baseline": "snapshots/debt.json"},
        str(workspace / ".lintro-config.yaml"),
    )
    assert_that(resolve_baseline_path(None, config)).is_equal_to(
        workspace / "snapshots/debt.json",
    )
    assert_that(resolve_baseline_path("override.json", config)).is_equal_to(
        workspace / "override.json",
    )
    project = workspace / "pyproject.toml"
    project.write_text('[tool.lintro]\nbaseline = "debt.json"\n', encoding="utf-8")
    loaded = build_config_from_dict(
        _convert_pyproject_to_config({"baseline": "debt.json"}),
        str(project),
    )
    assert_that(loaded.baseline).is_equal_to("debt.json")
    with pytest.raises(ValueError):
        build_config_from_dict({"baseline": False})


def test_format_baseline_applies_after_verify_without_counting_acknowledgements_as_fixes(
    workspace: Path,
) -> None:
    """Real format verification and saved reports count the filtered final state."""
    file = _project(workspace)
    runner = CliRunner()
    created = runner.invoke(cli, ["baseline", "create", "--tools", "ruff"])
    assert_that(created.exit_code).is_zero()
    formatted = runner.invoke(
        cli,
        [
            "fmt",
            "--tools",
            "ruff",
            "--baseline",
            ".lintro-baseline.json",
            "--output-format",
            "json",
            "--yes",
        ],
    )
    assert_that(formatted.exit_code).is_zero()
    report = json.loads(formatted.stdout)
    assert_that(file.read_text()).is_empty()
    assert_that(report["summary"]["total_remaining"]).is_zero()
    assert_that(report["summary"]["total_fixed"]).is_zero()
    assert_that(report["results"][0]["issues_count"]).is_zero()
    assert_that(report["results"][0]["remaining"]).is_zero()
