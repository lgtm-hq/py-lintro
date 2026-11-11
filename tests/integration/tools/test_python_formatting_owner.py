"""Exercise formatting fallback without changing the installed Black binary."""

from pathlib import Path

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli_utils.commands.check import check_command
from lintro.cli_utils.commands.format import format_command
from lintro.models.core.tool_snapshot import ToolSnapshot
from lintro.utils import tool_snapshots
from tests.integration._tools import require_tool

pytestmark = require_tool("ruff")


@pytest.mark.parametrize(
    "black_available",
    [False, pytest.param(True, marks=require_tool("black"))],
    ids=["missing", "installed"],
)
def test_python_formatting_owner_checks_and_fixes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    black_available: bool,
) -> None:
    """The runnable owner checks, fixes and reaches a stable formatting result.

    Args:
        tmp_path: Isolated fixture project.
        monkeypatch: Patch Black capability evidence without uninstalling it.
        black_available: Whether Black is allowed to run.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        "[tool.ruff.lint]\nselect = []\n[tool.lintro.execution]\nparallel = false\n",
    )
    target = tmp_path / "example.py"
    target.write_text('x = {  "a":1 }\n')
    original_probe = tool_snapshots.get_tool_snapshot

    def probe(
        name: str,
        *,
        force: bool = False,
        cache_root: Path | None = None,
    ) -> ToolSnapshot:
        """Hide Black's capability evidence only in the missing-tool case.

        Args:
            name: Requested tool.
            force: Whether to bypass cached evidence.
            cache_root: Directory anchoring the tool probe.

        Returns:
            Real tool evidence or a missing Black snapshot.
        """
        if name == "black" and not black_available:
            return ToolSnapshot(name="black", probe_error="not installed")
        return original_probe(name, force=force, cache_root=cache_root)

    monkeypatch.setattr(tool_snapshots, "get_tool_snapshot", probe)
    runner = CliRunner()
    args = [str(target), "--tools", "ruff,black", "--yes", "--no-art"]
    owner = "black" if black_available else "ruff"
    checked = runner.invoke(check_command, args)
    assert_that(checked.exit_code).is_equal_to(1)
    assert_that(checked.output).contains(f"{owner} owns Python formatting")
    if black_available:
        assert_that(checked.output).contains("ruff format_check not run")
    else:
        assert_that(checked.output).contains("black: not installed")

    explained = runner.invoke(check_command, [*args, "--explain-order"])
    assert_that(explained.exit_code).is_equal_to(0)
    assert_that(explained.output).contains(
        "Format ownership (1)",
        f"{owner} owns Python formatting",
    )
    fixed = runner.invoke(format_command, args)
    assert_that(fixed.exit_code).is_equal_to(0)
    assert_that(fixed.output).contains(f"{owner} owns Python formatting")
    assert_that(target.read_text()).is_equal_to('x = {"a": 1}\n')
    clean = runner.invoke(check_command, args)
    assert_that(clean.exit_code).is_equal_to(0)
    again = runner.invoke(format_command, args)
    assert_that(again.exit_code).is_equal_to(0)
    assert_that(target.read_text()).is_equal_to('x = {"a": 1}\n')


@require_tool("black")
def test_explanation_reports_cli_format_check_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI options override a disabled config in both explanation and execution.

    Args:
        tmp_path: Isolated project directory.
        monkeypatch: Working-directory isolation fixture.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        "[tool.ruff.lint]\nselect = []\n[tool.lintro.ruff]\nformat_check = false\n",
    )
    (tmp_path / "example.py").write_text('x = {  "a":1 }\n')
    args = [
        ".",
        "--tools",
        "ruff,black",
        "--tool-options",
        "ruff:format_check=True",
        "--yes",
    ]
    runner = CliRunner()
    explanation = runner.invoke(check_command, [*args, "--explain-order"])
    checked = runner.invoke(check_command, args)
    assert_that(explanation.exit_code).is_equal_to(0)
    assert_that(checked.exit_code).is_equal_to(1)
    for output in (explanation.output, checked.output):
        assert_that(output).contains("ruff format_check explicitly enabled")
        assert_that(output).does_not_contain("ruff format_check not run")


@require_tool("black")
def test_external_python_path_uses_project_probe_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Formatting probes and execution share the target project's cache root.

    Args:
        tmp_path: Parent of unrelated cwd and target project.
        monkeypatch: Environment and cache-location controls.
    """
    from lintro.tools.core import snapshots

    project = tmp_path / "project"
    unrelated = tmp_path / "unrelated"
    project.mkdir()
    unrelated.mkdir()
    (project / "pyproject.toml").write_text("[tool.ruff.lint]\nselect = []\n")
    target = project / "a.py"
    target.write_text('x = {  "a":1 }\n')
    monkeypatch.chdir(unrelated)

    def cache_path(*, cache_root: Path | None = None) -> Path:
        """Use production disk placement instead of the suite's isolated default.

        Args:
            cache_root: Explicit project root, or the process cwd.

        Returns:
            Production-equivalent cache file location inside the test directory.
        """
        return ((cache_root or Path.cwd()) / snapshots.SNAPSHOT_CACHE_RELPATH).resolve()

    monkeypatch.setattr(snapshots, "_cache_file_path", cache_path)
    result = CliRunner().invoke(
        check_command,
        [str(target), "--tools", "ruff,black", "--yes", "--no-art"],
    )
    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.output).contains("black owns Python formatting")
    assert_that((unrelated / ".lintro-cache").exists()).is_false()
    assert_that((project / snapshots.SNAPSHOT_CACHE_RELPATH).exists()).is_true()
