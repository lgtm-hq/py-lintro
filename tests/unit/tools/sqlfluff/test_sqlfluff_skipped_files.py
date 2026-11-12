"""Files SQLFluff skips are never claimed as inspected (#3388).

SQLFluff 4.5.0 prints one record per linted file, even a clean or empty one.
For a file it skips, it prints ``[]`` at exit 0 and a warning on stderr. It
skips a file its ``.sqlfluffignore`` excludes, and one larger than
``large_file_skip_byte_limit`` (20,000 bytes by default). A run where every
file was skipped inspected nothing, so it is "no files", not a clean pass,
and its reason names what SQLFluff said rather than guessing the cause.

The assertions read the result's output text rather than importing the
message constant, so this module fails on behaviour, not on import, against a
plugin without this handling.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.sqlfluff.definition import SqlfluffPlugin

IGNORED_WARNING = (
    "WARNING    Exact file path {path} was given but it was ignored by an ignore "
    "pattern set in .sqlfluffignore, re-run with `--disregard-sqlfluffignores` "
    "to not process ignore files.\n"
)
LARGE_FILE_WARNING = (
    "WARNING    Length of file '{path}' is 35280 bytes which is over the limit "
    "of 20000 bytes. Skipping to avoid parser lock.\n"
)
SKIPPED_EVERY_FILE = "SQLFluff skipped every file given"


def _run_sqlfluff(
    plugin: SqlfluffPlugin,
    tmp_path: Path,
    *,
    skipped: dict[str, str],
    names: tuple[str, ...],
    fixing: bool,
) -> ToolResult:
    """Run the real plugin with canned SQLFluff processes.

    Args:
        plugin: Plugin under test.
        tmp_path: Directory for the SQL files.
        skipped: File names SQLFluff skips, each mapped to the stderr warning
            template it prints for that file (empty for none).
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
        name = Path(path).name
        if name in skipped:
            stderr = skipped[name].format(path=path)
            stdout = "[]" if cmd[1] == "lint" else ""
            return SubprocessResult(0, stdout, stderr, stdout + stderr)
        stdout = (
            f'[{{"filepath": "{path}", "violations": []}}]' if cmd[1] == "lint" else ""
        )
        return SubprocessResult(0, stdout, "", stdout)

    with patch.object(BaseToolPlugin, "_run_subprocess_result", side_effect=respond):
        run = plugin.fix if fixing else plugin.check
        return run([str(tmp_path / name) for name in names], {})


@pytest.mark.parametrize(
    ("warning", "quoted", "not_quoted"),
    [
        (IGNORED_WARNING, "ignored by an ignore pattern set in .sqlfluffignore", ""),
        (
            LARGE_FILE_WARNING,
            "over the limit of 20000 bytes",
            "ignore pattern",
        ),
    ],
    ids=["sqlfluffignore", "large-file"],
)
def test_check_with_every_file_skipped_is_a_no_files_run(
    sqlfluff_plugin: SqlfluffPlugin,
    tmp_path: Path,
    warning: str,
    quoted: str,
    not_quoted: str,
) -> None:
    """``[]`` for every file: nothing inspected, and SQLFluff's reason quoted."""
    result = _run_sqlfluff(
        sqlfluff_plugin,
        tmp_path,
        skipped={"a.sql": warning, "b.sql": warning},
        names=("a.sql", "b.sql"),
        fixing=False,
    )

    assert_that(result.success).is_true()
    assert_that(result.no_files).is_true()
    assert_that(result.inspected_files).is_false()
    assert_that(result.execution_failed).is_false()
    assert_that(result.output).contains(SKIPPED_EVERY_FILE).contains(quoted)
    assert_that(result.output).does_not_contain("WARNING")
    if not_quoted:
        assert_that(result.output).does_not_contain(not_quoted)


def test_skip_without_a_warning_keeps_the_generic_reason(
    sqlfluff_plugin: SqlfluffPlugin,
    tmp_path: Path,
) -> None:
    """With no stderr warning the reason names both causes, not one guess."""
    result = _run_sqlfluff(
        sqlfluff_plugin,
        tmp_path,
        skipped={"a.sql": ""},
        names=("a.sql",),
        fixing=False,
    )

    assert_that(result.no_files).is_true()
    assert_that(result.output).contains(SKIPPED_EVERY_FILE).contains(
        "large_file_skip_byte_limit",
    ).ends_with("none was inspected.")


def test_fix_with_every_file_skipped_records_zero_counters(
    sqlfluff_plugin: SqlfluffPlugin,
    tmp_path: Path,
) -> None:
    """A fix run over skipped files fixed nothing and inspected nothing."""
    result = _run_sqlfluff(
        sqlfluff_plugin,
        tmp_path,
        skipped={"a.sql": IGNORED_WARNING},
        names=("a.sql",),
        fixing=True,
    )

    assert_that(result.no_files).is_true()
    assert_that(result.inspected_files).is_false()
    assert_that(
        (result.fixed_issues_count, result.remaining_issues_count),
    ).is_equal_to((0, 0))
    assert_that(result.output).contains(SKIPPED_EVERY_FILE)


def test_a_linted_file_keeps_the_inspection_claim(
    sqlfluff_plugin: SqlfluffPlugin,
    tmp_path: Path,
) -> None:
    """One file with a record proves the run inspected files."""
    result = _run_sqlfluff(
        sqlfluff_plugin,
        tmp_path,
        skipped={"a.sql": LARGE_FILE_WARNING},
        names=("a.sql", "b.sql"),
        fixing=False,
    )

    assert_that(result.success).is_true()
    assert_that(result.no_files).is_false()
    assert_that(result.inspected_files).is_true()


def test_an_earlier_skipped_run_does_not_leak_into_the_next(
    sqlfluff_plugin: SqlfluffPlugin,
    tmp_path: Path,
) -> None:
    """The same instance forgets which files an earlier run saw skipped."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _run_sqlfluff(
        sqlfluff_plugin,
        first,
        skipped={"a.sql": IGNORED_WARNING},
        names=("a.sql",),
        fixing=False,
    )

    result = _run_sqlfluff(
        sqlfluff_plugin,
        second,
        skipped={},
        names=("a.sql",),
        fixing=False,
    )

    assert_that(result.no_files).is_false()
    assert_that(result.inspected_files).is_true()
