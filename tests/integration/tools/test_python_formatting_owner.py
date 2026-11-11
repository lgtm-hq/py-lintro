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
        assert_that(checked.output).contains("black unavailable")

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
