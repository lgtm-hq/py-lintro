"""Default and mixed selections preserve uncertified producer failures."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli
from lintro.config.config_loader import clear_config_cache
from lintro.models.core.run_artifact import RunArtifact
from lintro.models.core.tool_result import ToolResult
from lintro.tools.mypy.definition import MypyPlugin
from lintro.tools.ruff.definition import RuffPlugin
from lintro.utils.fingerprint_baseline import snapshot
from lintro.utils.fingerprint_baseline_file import change_baseline, read_baseline


@pytest.fixture
def mixed_project(workspace: Path) -> Path:
    """Configure default Ruff/mypy selection with one real finding from each."""
    (workspace / ".lintro-config.yaml").write_text(
        "execution:\n"
        "  enabled_tools: [ruff, mypy]\n"
        "  auto_install_deps: false\n"
        "  parallel: false\n",
        encoding="utf-8",
    )
    (workspace / "pyproject.toml").write_text(
        '[tool.ruff.lint]\nselect = ["F401"]\n'
        "[tool.lintro.ruff]\nformat_check = false\n",
        encoding="utf-8",
    )
    (workspace / "example.py").write_text(
        'import os\nvalue: int = "wrong"\n',
        encoding="utf-8",
    )
    clear_config_cache()
    return workspace


@pytest.mark.parametrize("selection", [[], ["--tools", "ruff,mypy"]])
@pytest.mark.parametrize("mode", ["create", "update", "prune"])
def test_management_rejects_uncertified_findings_without_partial_write(
    mixed_project: Path,
    selection: list[str],
    mode: str,
) -> None:
    """Real mypy findings block the whole default or mixed management transaction."""
    runner = CliRunner()
    path = mixed_project / ".lintro-baseline.json"
    before = None
    if mode != "create":
        seeded = runner.invoke(cli, ["baseline", "create", "--tools", "ruff"])
        assert_that(seeded.exit_code).is_zero()
        assert_that(read_baseline(path).entries).is_length(1)
        before = path.read_bytes()

    result = runner.invoke(cli, ["baseline", mode, *selection])

    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.output).contains(
        "Baseline unchanged: mypy: producer does not certify complete findings",
        "Use --tools to select supported tools",
    )
    if before is None:
        assert_that(path.exists()).is_false()
    else:
        assert_that(path.read_bytes()).is_equal_to(before)
    assert_that((mixed_project / "example.py").read_text()).is_equal_to(
        'import os\nvalue: int = "wrong"\n',
    )


@pytest.mark.parametrize("selection", [[], ["--tools", "ruff,mypy"]])
def test_check_acknowledges_certified_peer_but_keeps_real_mypy_failure(
    mixed_project: Path,
    selection: list[str],
) -> None:
    """Even an imported matching mypy entry cannot explain its failed verdict."""
    file = str(mixed_project / "example.py")
    ruff = RuffPlugin()
    ruff.set_options(format_check=False)
    mypy_result = MypyPlugin().check([file], {})
    assert_that(mypy_result.success).is_false()
    assert_that(mypy_result.findings_complete).is_false()
    # Simulate a baseline imported from a producer that previously certified mypy.
    baseline = snapshot(
        [ruff.check([file], {}), replace(mypy_result, findings_complete=True)],
        root=mixed_project,
    )
    baseline_tools = {entry.tool for entry in baseline.entries}
    assert_that(baseline_tools).contains_only("ruff", "mypy")
    path = mixed_project / ".lintro-baseline.json"
    change_baseline(path, lambda _: baseline, create=True)
    before = path.read_bytes()

    checked = CliRunner().invoke(
        cli,
        [
            "chk",
            *selection,
            "--baseline",
            str(path),
            "--output-format",
            "json",
            "--yes",
        ],
    )

    assert_that(checked.exit_code).is_equal_to(1)
    report = json.loads(checked.stdout)
    results = {result["tool"]: result for result in report["results"]}
    assert_that(results["ruff"]["baselined_count"]).is_equal_to(1)
    assert_that(results["ruff"]["issues_count"]).is_zero()
    assert_that(results["mypy"]["baselined_count"]).is_zero()
    assert_that(results["mypy"]["issues_count"]).is_equal_to(mypy_result.issues_count)
    assert_that(results["mypy"]["issues"]).is_not_empty()
    assert_that(report["summary"]["total_baselined"]).is_equal_to(1)
    assert_that(checked.stderr).contains(
        "Baseline not applied to mypy: producer does not certify complete findings",
    )
    assert_that(path.read_bytes()).is_equal_to(before)


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        ({"inspected_files": None}, "file inspection was not confirmed"),
        ({"timed_out": True}, "execution timed out"),
        ({"execution_failed": True}, "tool execution failed"),
        ({"parse_failures_count": 2}, "report has 2 parse failure(s)"),
        ({"issues_count": 2}, "finding count does not match the parsed report"),
        (
            {"residual_unknown": True, "residual_unknown_reason": "verify failed"},
            "residual findings are unknown",
        ),
        (
            {"issues": [], "issues_count": 0},
            "failed verdict is not explained by findings",
        ),
    ],
)
def test_management_names_every_blocker_without_dropping_failed_tool(
    workspace: Path,
    dirty_result: ToolResult,
    monkeypatch: pytest.MonkeyPatch,
    state: dict[str, Any],
    reason: str,
) -> None:
    """A complete peer never allows incomplete or uncertified results to be skipped."""
    monkeypatch.setattr(
        "lintro.cli_utils.commands.baseline.execute_run",
        lambda **_: RunArtifact(
            workspace_root=workspace,
            tool_results=[
                dirty_result,
                replace(dirty_result, name="mypy", findings_complete=False),
                replace(dirty_result, name="black", **state),
            ],
        ),
    )

    result = CliRunner().invoke(cli, ["baseline", "create"])

    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.output).contains(
        "mypy: producer does not certify complete findings for baselining",
        f"black: {reason}",
    )
    assert_that((workspace / ".lintro-baseline.json").exists()).is_false()


@pytest.mark.parametrize("subcommand", [[], ["create"], ["update"], ["prune"]])
def test_management_help_documents_certified_tools_and_mixed_selection(
    subcommand: list[str],
) -> None:
    """Each management entry point exposes the supported producer list and limits."""
    result = CliRunner().invoke(cli, ["baseline", *subcommand, "--help"])
    text = " ".join(result.output.split())

    assert_that(result.exit_code).is_zero()
    assert_that(text).contains(
        "ruff, black, shellcheck, hadolint, shfmt, dotenv-linter, pydoclint, sqlfluff",
        "Default or mixed selections abort",
        "no failing tool is silently omitted",
    )


@pytest.mark.parametrize("command", ["chk", "fmt"])
def test_baseline_option_help_explains_unsupported_findings_remain_failing(
    command: str,
) -> None:
    """Filtering help points users to the certified tool list and preserves limits."""
    result = CliRunner().invoke(cli, [command, "--help"])
    text = " ".join(result.output.split())

    assert_that(result.exit_code).is_zero()
    assert_that(text).contains(
        "Unsupported or incomplete findings remain visible and failing",
        "lintro baseline --help",
    )
