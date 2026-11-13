"""A real SQLFluff write that fails stays unmeasured through a clean retry (#3401).

``sqlfluff fix`` exits 1 both when unfixable violations remain and when it
cannot write, printing a ``PermissionError`` traceback. The directory is
read-only for the first write only, so the convergence retry could run clean.
The failed write must still end the run unmeasured, while an ordinary
"unfixable violations remain" exit stays a measured result.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.models.core.subprocess_result import SubprocessResult
from lintro.tools.sqlfluff.definition import SqlfluffPlugin
from lintro.utils.execution.fix_retry import run_fix_with_retry
from tests.integration._tools import require_command

pytestmark = [
    require_command("sqlfluff", ["sqlfluff"]),
    pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="root ignores directory permissions",
    ),
]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Create a project directory with an ANSI dialect configuration.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        The project directory.
    """
    directory = tmp_path / "project"
    directory.mkdir()
    (directory / ".sqlfluff").write_text("[sqlfluff]\ndialect = ansi\n")
    return directory


def _restore_after_first_write(
    plugin: SqlfluffPlugin,
    directory: Path,
) -> Callable[..., SubprocessResult]:
    """Run SQLFluff for real, making the directory writable after one fix.

    Args:
        plugin: Plugin whose real subprocess runner is wrapped.
        directory: Directory made writable again after the first fix.

    Returns:
        A stand-in for ``_run_subprocess_result``.
    """
    real = plugin._run_subprocess_result

    def run(*args: Any, **kwargs: Any) -> SubprocessResult:
        process = real(*args, **kwargs)
        cmd = kwargs.get("cmd", args[0] if args else [])
        if cmd[1:2] == ["fix"]:
            directory.chmod(stat.S_IRWXU)
        return process

    return run


#: A writable name SQLFluff echoes on stderr across two lines, the second
#: shaped like an exception line with error words in it (#3414).
_SPLIT_NAME = "query\nPermissionError: error permission denied.sql"


@pytest.mark.parametrize("name", ["query.sql", _SPLIT_NAME])
def test_permission_error_then_clean_retry_stays_unmeasured(
    project: Path,
    name: str,
) -> None:
    """The failed write is not replaced by the retry that could have run.

    A file name that splits into an exception-shaped line on stderr does not
    hide the real traceback (#3414).
    """
    query = project / name
    query.write_text("select  1\n")
    project.chmod(stat.S_IRUSR | stat.S_IXUSR)
    plugin = SqlfluffPlugin()
    try:
        with patch.object(
            plugin,
            "_run_subprocess_result",
            side_effect=_restore_after_first_write(plugin, project),
        ):
            result = run_fix_with_retry(plugin, [str(query)], {}, 3)
    finally:
        project.chmod(stat.S_IRWXU)

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.residual_unknown_reason).is_equal_to(
        "SQLFluff fix did not complete.",
    )
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
    assert_that(result.issues).is_not_empty()
    assert_that(query.read_text()).is_equal_to("select  1\n")


_NAMES = ["query.sql", "ENOSPC.sql", "PermissionError.sql"]


@pytest.mark.parametrize("name", _NAMES)
def test_fixable_violation_is_fixed_and_measured(project: Path, name: str) -> None:
    """A clean ``sqlfluff fix`` is measured whatever the file is called.

    ``sqlfluff fix`` prints the path on stdout; a name that looks like an
    error class is not a diagnostic.
    """
    query = project / name
    query.write_text("select  1;\n")

    result = run_fix_with_retry(SqlfluffPlugin(), [str(query)], {}, 3)

    assert_that(result.success).is_true()
    assert_that(result.execution_failed).is_false()
    assert_that(result.residual_unknown).is_false()
    assert_that(result.initial_issues_count).is_greater_than(0)
    assert_that(result.fixed_issues_count).is_equal_to(result.initial_issues_count)
    assert_that(result.remaining_issues_count).is_zero()
    assert_that(query.read_text()).is_equal_to("select 1;\n")


@pytest.mark.parametrize("name", _NAMES)
def test_unfixable_violations_stay_measured(project: Path, name: str) -> None:
    """Exit 1 for violations ``sqlfluff fix`` cannot fix is a measured result."""
    query = project / name
    query.write_text("SELECT * FROM t1 JOIN t2\n")

    result = run_fix_with_retry(SqlfluffPlugin(), [str(query)], {}, 3)

    assert_that(result.execution_failed).is_false()
    assert_that(result.residual_unknown).is_false()
    assert_that(result.residual_unknown_reason).is_none()
    assert_that(result.remaining_issues_count).is_greater_than(0)


@pytest.mark.parametrize("name", ["query.sql", _SPLIT_NAME])
def test_parse_errors_stay_measured_when_stderr_echoes_the_path(
    project: Path,
    name: str,
) -> None:
    """A parse error echoes the path on stderr; that echo is not a diagnostic.

    ``sqlfluff fix`` exits 1 and prints ``== [<path>] ==`` on stderr. A name
    with a newline puts an exception-shaped fragment at the start of a line,
    which must not turn the ordinary exit into an execution failure (#3414).
    """
    query = project / name
    query.write_text("SELECT a FROM b WHERE (\n")

    result = run_fix_with_retry(SqlfluffPlugin(), [str(query)], {}, 3)

    assert_that(result.execution_failed).is_false()
    assert_that(result.residual_unknown).is_false()
    assert_that(result.fixed_issues_count).is_zero()
    assert_that(result.remaining_issues_count).is_greater_than(0)
