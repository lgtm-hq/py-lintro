"""Real dotenv-linter fix exits are classified by status, not filename (#3401).

``dotenv-linter fix`` exits 0 once it has fixed what it can and 1 when it
cannot write. A file whose name looks like an error class is fixed and
measured like any other; a real permission failure stays unmeasured through a
clean convergence retry.
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
from lintro.tools.dotenv_linter.definition import DotenvLinterPlugin
from lintro.utils.execution.fix_retry import run_fix_with_retry
from tests.integration._tools import require_command

pytestmark = [
    require_command(
        "dotenv-linter",
        DotenvLinterPlugin()._get_executable_command(tool_name="dotenv-linter"),
    ),
    pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="root ignores file permissions",
    ),
]

_DIRTY = "foo=1\n"


@pytest.mark.parametrize("name", ["ENOSPC.env", "PermissionError.env"])
def test_diagnostic_looking_name_is_fixed_and_measured(
    tmp_path: Path,
    name: str,
) -> None:
    """A clean ``dotenv-linter fix`` is measured whatever the file is called."""
    env_file = tmp_path / name
    env_file.write_text(_DIRTY)

    result = run_fix_with_retry(DotenvLinterPlugin(), [str(env_file)], {}, 3)

    assert_that(result.success).is_true()
    assert_that(result.execution_failed).is_false()
    assert_that(result.residual_unknown).is_false()
    assert_that(result.fixed_issues_count).is_equal_to(result.initial_issues_count)
    assert_that(env_file.read_text()).is_equal_to("FOO=1\n")


def _restore_after_first_fix(
    plugin: DotenvLinterPlugin,
    env_file: Path,
) -> Callable[..., SubprocessResult]:
    """Run dotenv-linter for real, making the file writable after one fix.

    Args:
        plugin: Plugin whose real subprocess runner is wrapped.
        env_file: File made writable again after the first fix.

    Returns:
        A stand-in for ``_run_subprocess_result``.
    """
    real = plugin._run_subprocess_result

    def run(*args: Any, **kwargs: Any) -> SubprocessResult:
        process = real(*args, **kwargs)
        cmd = kwargs.get("cmd", args[0] if args else [])
        if "fix" in cmd:
            env_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
        return process

    return run


def test_permission_denied_then_clean_retry_stays_unmeasured(tmp_path: Path) -> None:
    """The failed write is not replaced by the retry that could have run."""
    env_file = tmp_path / "ENOSPC.env"
    env_file.write_text(_DIRTY)
    env_file.chmod(stat.S_IRUSR)
    plugin = DotenvLinterPlugin()
    try:
        with patch.object(
            plugin,
            "_run_subprocess_result",
            side_effect=_restore_after_first_fix(plugin, env_file),
        ):
            result = run_fix_with_retry(plugin, [str(env_file)], {}, 3)
    finally:
        env_file.chmod(stat.S_IRUSR | stat.S_IWUSR)

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
    assert_that(env_file.read_text()).is_equal_to(_DIRTY)
