"""CLI wiring tests for the ``--diff`` option on check and format."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that
from click.testing import CliRunner, Result

from lintro.cli import cli
from lintro.utils.git_diff import DIFF_DEFAULT_SENTINEL
from tests.unit.conftest import run_git


@pytest.mark.parametrize("command", ["chk", "fmt"])
def test_diff_option_in_help(command: str) -> None:
    """The ``--diff`` option is documented in check and format help.

    Args:
        command: CLI subcommand alias under test.
    """
    runner = CliRunner()
    result = runner.invoke(cli, [command, "--help"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("--diff")


def _git(*args: str, cwd: Path) -> None:
    """Run one git command in a throwaway repository.

    Delegates to :func:`tests.unit.conftest.run_git`, which blanks
    ``GIT_CONFIG_GLOBAL``/``GIT_CONFIG_SYSTEM`` and drops a leaked
    ``GIT_DIR``/``GIT_INDEX_FILE``/``GIT_WORK_TREE``. Without that, a
    developer's global ``commit.gpgsign``, ``core.hooksPath`` or
    ``init.templateDir`` changes how these fixtures behave from machine to
    machine (#2315).

    Args:
        *args: Arguments after the ``git`` executable.
        cwd: Repository directory to run in.
    """
    run_git(["git", *args], cwd=cwd)


def _assert_no_crash(*, result: Result) -> None:
    """Assert a CLI invocation ended by exiting, not by raising.

    ``CliRunner`` swallows exceptions into ``result.exception``, so string
    assertions on ``result.output`` still pass when the command crashed after
    printing. A normal non-zero exit surfaces here as ``SystemExit``, which is
    a ``BaseException`` rather than an ``Exception``; anything that is an
    ``Exception`` is a real crash.

    Args:
        result: Result returned by ``CliRunner.invoke``.
    """
    assert_that(isinstance(result.exception, Exception)).described_as(
        f"command crashed: {result.exception!r}",
    ).is_false()


def _repo_with_one_changed_file(*, tmp_path: Path) -> Path:
    """Build a git repo whose committed and uncommitted files both lint dirty.

    ``baseline.py`` is committed on ``main`` and carries an unused import;
    ``changed.py`` carries another and is staged but not committed. Diff mode
    must therefore report ``changed.py`` alone, while a full scan reports both.

    Args:
        tmp_path: Pytest temporary directory to build the repository in.

    Returns:
        Path: The repository root.
    """
    _git("init", "-q", cwd=tmp_path)
    (tmp_path / "baseline.py").write_text("import os\n", encoding="utf-8")
    _git("add", "-A", cwd=tmp_path)
    _git("commit", "-qm", "init", cwd=tmp_path)
    _git("branch", "-M", "main", cwd=tmp_path)
    (tmp_path / "changed.py").write_text("import sys\n", encoding="utf-8")
    _git("add", "-A", cwd=tmp_path)
    return tmp_path


def test_diff_flag_without_value_scans_only_changes_vs_the_default_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``chk --diff`` resolves a default base and reports only the change.

    The unused import in ``changed.py`` makes the run exit 1.

    Args:
        tmp_path: Pytest temporary directory for the throwaway repository.
        monkeypatch: Pytest monkeypatch fixture, used to enter the repository.
    """
    repo = _repo_with_one_changed_file(tmp_path=tmp_path)
    monkeypatch.chdir(repo)

    result = CliRunner().invoke(cli, ["chk", "--diff", "--tools", "ruff"])

    _assert_no_crash(result=result)
    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.output).contains("Diff mode")
    assert_that(result.output).contains("default base")
    assert_that(result.output).contains("changed.py")
    assert_that(result.output).does_not_contain("baseline.py")


def test_diff_flag_with_explicit_base_scans_only_changes_vs_that_ref(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``chk --diff main`` reports the change and skips the committed file.

    The unused import in ``changed.py`` makes the run exit 1.

    Args:
        tmp_path: Pytest temporary directory for the throwaway repository.
        monkeypatch: Pytest monkeypatch fixture, used to enter the repository.
    """
    repo = _repo_with_one_changed_file(tmp_path=tmp_path)
    monkeypatch.chdir(repo)

    result = CliRunner().invoke(cli, ["chk", "--diff", "main", "--tools", "ruff"])

    _assert_no_crash(result=result)
    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.output).contains("Diff mode")
    assert_that(result.output).contains("vs main")
    assert_that(result.output).contains("changed.py")
    assert_that(result.output).does_not_contain("baseline.py")


def test_no_diff_flag_scans_every_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting ``--diff`` scans the whole tree, committed files included.

    Both files carry an unused import, so the run exits 1.

    Args:
        tmp_path: Pytest temporary directory for the throwaway repository.
        monkeypatch: Pytest monkeypatch fixture, used to enter the repository.
    """
    repo = _repo_with_one_changed_file(tmp_path=tmp_path)
    monkeypatch.chdir(repo)

    result = CliRunner().invoke(cli, ["chk", "--tools", "ruff"])

    _assert_no_crash(result=result)
    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.output).does_not_contain("Diff mode")
    assert_that(result.output).contains("changed.py")
    assert_that(result.output).contains("baseline.py")


def test_format_diff_flag_fixes_only_the_changed_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``fmt --diff`` rewrites the changed file and leaves the rest alone.

    Args:
        tmp_path: Pytest temporary directory for the throwaway repository.
        monkeypatch: Pytest monkeypatch fixture, used to enter the repository.
    """
    repo = _repo_with_one_changed_file(tmp_path=tmp_path)
    monkeypatch.chdir(repo)

    result = CliRunner().invoke(cli, ["fmt", "--diff", "--tools", "ruff"])

    _assert_no_crash(result=result)
    assert_that(result.exit_code).is_equal_to(0)
    assert_that((repo / "changed.py").read_text(encoding="utf-8")).does_not_contain(
        "import sys",
    )
    assert_that((repo / "baseline.py").read_text(encoding="utf-8")).is_equal_to(
        "import os\n",
    )


def test_diff_equals_syntax_passes_ref() -> None:
    """``chk --diff=main`` forwards the explicit base ref."""
    runner = CliRunner()
    with patch(
        "lintro.cli_utils.commands.check.run_lint_with_ai",
        return_value=0,
    ) as mock_run:
        result = runner.invoke(cli, ["chk", "--diff=main", "--tools", "ruff"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(mock_run.call_args.kwargs["diff_base"]).is_equal_to("main")


def test_diff_rejects_existing_path_as_base(tmp_path: Path) -> None:
    """``chk --diff <path>`` errors instead of consuming the scan path."""
    scan_dir = tmp_path / "src"
    scan_dir.mkdir()
    runner = CliRunner()
    with patch(
        "lintro.cli_utils.commands.check.run_lint_with_ai",
        return_value=0,
    ) as mock_run:
        result = runner.invoke(
            cli,
            ["chk", "--diff", str(scan_dir), "--tools", "ruff"],
        )

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).contains("looks like a filesystem path")
    assert_that(result.output).contains("--diff=<ref>")
    assert_that(mock_run.called).is_false()


def test_diff_with_separator_treats_path_as_scan_target(tmp_path: Path) -> None:
    """``chk --diff -- <path>`` keeps the path as a scan target."""
    scan_dir = tmp_path / "src"
    scan_dir.mkdir()
    runner = CliRunner()
    with patch(
        "lintro.cli_utils.commands.check.run_lint_with_ai",
        return_value=0,
    ) as mock_run:
        result = runner.invoke(
            cli,
            ["chk", "--diff", "--tools", "ruff", "--", str(scan_dir)],
        )

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(mock_run.call_args.kwargs["diff_base"]).is_equal_to(
        DIFF_DEFAULT_SENTINEL,
    )
    assert_that(mock_run.call_args.kwargs["paths"]).contains(str(scan_dir))


def test_diff_equals_syntax_allows_ref_when_path_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--diff=main`` works when a ``main/`` directory also exists."""
    _git("init", "-q", cwd=tmp_path)
    _git("commit", "--allow-empty", "-qm", "init", cwd=tmp_path)
    _git("branch", "-M", "main", cwd=tmp_path)
    (tmp_path / "main").mkdir()
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    with patch(
        "lintro.cli_utils.commands.check.run_lint_with_ai",
        return_value=0,
    ) as mock_run:
        result = runner.invoke(cli, ["chk", "--diff=main", "--tools", "ruff"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(mock_run.call_args.kwargs["diff_base"]).is_equal_to("main")
