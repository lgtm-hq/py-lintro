"""Real-binary baseline regression coverage."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from lintro.cli import cli
from lintro.utils.fingerprint_baseline_file import read_baseline
from tests.integration._tools import require_tool


@pytest.mark.parametrize("tool", ["ruff", "black"])
@pytest.mark.parametrize("mode", ["update", "prune"])
def test_fixed_formatter_file_loses_acknowledgement_before_regression(
    workspace: Path,
    tool: str,
    mode: str,
) -> None:
    """Real create/check/maintenance/check rejects previously fixed format debt."""
    file = workspace / "example.py"
    file.write_text("x=1\n", encoding="utf-8")
    runner = CliRunner()
    created = runner.invoke(cli, ["baseline", "create", file.name, "--tools", tool])
    assert created.exit_code == 0, created.output
    baseline = workspace / ".lintro-baseline.json"
    assert len(read_baseline(baseline).entries) == 1
    check_args = [
        "chk",
        file.name,
        "--tools",
        tool,
        "--baseline",
        str(baseline),
        "--yes",
    ]
    acknowledged = runner.invoke(cli, check_args)
    assert acknowledged.exit_code == 0, acknowledged.output
    assert "0 issues (1 baselined)" in acknowledged.output

    file.write_text("x = 1\n", encoding="utf-8")
    maintained = runner.invoke(cli, ["baseline", mode, file.name, "--tools", tool])
    assert maintained.exit_code == 0, maintained.output
    assert read_baseline(baseline).entries == []

    file.write_text("x=1\n", encoding="utf-8")
    regressed = runner.invoke(cli, check_args)
    assert regressed.exit_code == 1, regressed.output
    assert "1 issues (0 baselined)" in regressed.output


@pytest.mark.parametrize("tool", ["ruff", "black"])
@pytest.mark.parametrize("mode", ["update", "prune"])
def test_native_formatter_exclusion_retains_acknowledgement(
    workspace: Path,
    tool: str,
    mode: str,
) -> None:
    """Lint discovery and a zero formatter exit cannot prove an excluded file."""
    file = workspace / "example.py"
    file.write_text("x=1\n", encoding="utf-8")
    runner = CliRunner()
    created = runner.invoke(cli, ["baseline", "create", file.name, "--tools", tool])
    assert created.exit_code == 0, created.output
    baseline = workspace / ".lintro-baseline.json"
    before = baseline.read_bytes()
    native_config = (
        "[tool.ruff]\nforce-exclude = true\n"
        '[tool.ruff.format]\nexclude = ["example.py"]\n'
        if tool == "ruff"
        else '[tool.black]\nforce-exclude = "example.py"\n'
    )
    (workspace / "pyproject.toml").write_text(native_config, encoding="utf-8")
    file.write_text("x = 1\n", encoding="utf-8")

    maintained = runner.invoke(cli, ["baseline", mode, file.name, "--tools", tool])

    assert maintained.exit_code == 0, maintained.output
    assert baseline.read_bytes() == before


@pytest.mark.parametrize("mode", ["update", "prune"])
@pytest.mark.parametrize("separator", ["\n", "\r"])
def test_black_multiline_filename_cannot_prune_excluded_victim(
    workspace: Path,
    mode: str,
    separator: str,
) -> None:
    """A native confirmation's filename suffix cannot erase excluded format debt."""
    victim = workspace / "victim.py"
    victim.write_text("x=1\n", encoding="utf-8")
    runner = CliRunner()
    created = runner.invoke(
        cli,
        ["baseline", "create", victim.name, "--tools", "black"],
    )
    assert created.exit_code == 0, created.output
    baseline = workspace / ".lintro-baseline.json"
    assert len(read_baseline(baseline).entries) == 1
    before = baseline.read_bytes()
    (workspace / "pyproject.toml").write_text(
        '[tool.black]\nforce-exclude = "/victim[.]py$"\n',
        encoding="utf-8",
    )
    # Black excludes victim.py, but prints this clean file's name verbatim.
    (workspace / f"prefix{separator}victim.py").write_text("x = 1\n", encoding="utf-8")

    maintained = runner.invoke(cli, ["baseline", mode, ".", "--tools", "black"])

    assert maintained.exit_code == 0, maintained.output
    assert baseline.read_bytes() == before


pytestmark = [require_tool("ruff"), require_tool("black")]
