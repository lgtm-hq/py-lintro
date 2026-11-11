"""Real-binary baseline regression coverage."""

import json
from pathlib import Path

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli
from lintro.config.config_loader import (
    clear_config_cache,
)
from lintro.utils.fingerprint_baseline_file import (
    read_baseline,
)
from tests.integration._tools import require_tool


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


pytestmark = [require_tool("ruff")]
