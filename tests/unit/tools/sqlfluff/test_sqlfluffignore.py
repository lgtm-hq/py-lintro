"""Files ``.sqlfluffignore`` excludes are never claimed as inspected (#3388).

SQLFluff 4.5.0 prints one record per linted file, even a clean or empty one.
Given one file its ``.sqlfluffignore`` excludes, it logs a warning on stderr
and prints ``[]`` at exit 0. A run where every file was ignored inspected
nothing, so it is "no files", not a clean pass.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.sqlfluff.definition import (
    SQLFLUFF_ALL_IGNORED_MESSAGE,
    SqlfluffPlugin,
)

IGNORED_WARNING = (
    "WARNING    Exact file path {path} was given but it was ignored by an ignore "
    "pattern set in .sqlfluffignore\n"
)


def _run_sqlfluff(
    plugin: SqlfluffPlugin,
    tmp_path: Path,
    *,
    ignored: set[str],
    names: tuple[str, ...],
    fixing: bool,
) -> ToolResult:
    """Run the real plugin with canned SQLFluff processes.

    Args:
        plugin: Plugin under test.
        tmp_path: Directory for the SQL files.
        ignored: File names ``.sqlfluffignore`` excludes.
        names: SQL files to create and pass.
        fixing: Run ``fix`` instead of ``check``.

    Returns:
        The plugin's ToolResult.
    """
    for name in names:
        (tmp_path / name).write_text("SELECT 1\n")

    def respond(cmd: list[str], *_args: object, **_kwargs: object) -> SubprocessResult:
        """Answer one SQLFluff invocation the way SQLFluff 4.5.0 does.

        Args:
            cmd: The command lintro built.
            *_args: Unused positional subprocess arguments.
            **_kwargs: Unused keyword subprocess arguments.

        Returns:
            The canned result for the file named last in ``cmd``.
        """
        path = cmd[-1]
        if Path(path).name in ignored:
            stderr = IGNORED_WARNING.format(path=path)
            stdout = "[]" if cmd[1] == "lint" else ""
            return SubprocessResult(0, stdout, stderr, stdout + stderr)
        stdout = (
            f'[{{"filepath": "{path}", "violations": []}}]' if cmd[1] == "lint" else ""
        )
        return SubprocessResult(0, stdout, "", stdout)

    with patch.object(BaseToolPlugin, "_run_subprocess_result", side_effect=respond):
        run = plugin.fix if fixing else plugin.check
        return run([str(tmp_path / name) for name in names], {})


def test_check_with_every_file_ignored_is_a_no_files_run(
    sqlfluff_plugin: SqlfluffPlugin,
    tmp_path: Path,
) -> None:
    """``[]`` for every file: nothing inspected, and the reason is named."""
    result = _run_sqlfluff(
        sqlfluff_plugin,
        tmp_path,
        ignored={"a.sql", "b.sql"},
        names=("a.sql", "b.sql"),
        fixing=False,
    )

    assert_that(result.success).is_true()
    assert_that(result.no_files).is_true()
    assert_that(result.inspected_files).is_false()
    assert_that(result.execution_failed).is_false()
    assert_that(result.output).is_equal_to(SQLFLUFF_ALL_IGNORED_MESSAGE)


def test_fix_with_every_file_ignored_records_zero_counters(
    sqlfluff_plugin: SqlfluffPlugin,
    tmp_path: Path,
) -> None:
    """A fix run over ignored files fixed nothing and inspected nothing."""
    result = _run_sqlfluff(
        sqlfluff_plugin,
        tmp_path,
        ignored={"a.sql"},
        names=("a.sql",),
        fixing=True,
    )

    assert_that(result.no_files).is_true()
    assert_that(result.inspected_files).is_false()
    assert_that(
        (result.fixed_issues_count, result.remaining_issues_count),
    ).is_equal_to((0, 0))
    assert_that(result.output).is_equal_to(SQLFLUFF_ALL_IGNORED_MESSAGE)


def test_a_linted_file_keeps_the_inspection_claim(
    sqlfluff_plugin: SqlfluffPlugin,
    tmp_path: Path,
) -> None:
    """One file with a record proves the run inspected files."""
    result = _run_sqlfluff(
        sqlfluff_plugin,
        tmp_path,
        ignored={"a.sql"},
        names=("a.sql", "b.sql"),
        fixing=False,
    )

    assert_that(result.success).is_true()
    assert_that(result.no_files).is_false()
    assert_that(result.inspected_files).is_true()


def test_an_earlier_ignored_run_does_not_leak_into_the_next(
    sqlfluff_plugin: SqlfluffPlugin,
    tmp_path: Path,
) -> None:
    """The same instance forgets which files an earlier run saw ignored."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _run_sqlfluff(
        sqlfluff_plugin,
        first,
        ignored={"a.sql"},
        names=("a.sql",),
        fixing=False,
    )

    result = _run_sqlfluff(
        sqlfluff_plugin,
        second,
        ignored=set(),
        names=("a.sql",),
        fixing=False,
    )

    assert_that(result.no_files).is_false()
    assert_that(result.inspected_files).is_true()
