"""CLI lifecycle with real Ruff and conservative maintenance scans."""

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
