"""A real shfmt write that fails stays unmeasured through a clean retry (#3401).

``shfmt -w`` exits 1 with ``permission denied`` when it cannot write the file.
The directory is read-only for the first write only; permissions come back
before the convergence retry, so the retry itself would be clean. The run must
still end as an unmeasured execution failure, and the file stays unformatted.
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
from lintro.tools.shfmt.definition import ShfmtPlugin
from lintro.utils.execution.fix_retry import run_fix_with_retry
from tests.integration._tools import require_command

pytestmark = [
    require_command(
        "shfmt",
        ShfmtPlugin()._get_executable_command(tool_name="shfmt"),
    ),
    pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="root ignores directory permissions",
    ),
]

_DIRTY = '#!/bin/bash\nif [ "$1" = "x" ];then\necho  "hi"\nfi\n'


def _restore_after_first_write(
    plugin: ShfmtPlugin,
    directory: Path,
) -> Callable[..., SubprocessResult]:
    """Run shfmt for real, making the directory writable after the first write.

    Args:
        plugin: Plugin whose real subprocess runner is wrapped.
        directory: Directory made writable again after the first ``-w`` run.

    Returns:
        A stand-in for ``_run_subprocess_result``.
    """
    real = plugin._run_subprocess_result

    def run(*args: Any, **kwargs: Any) -> SubprocessResult:
        process = real(*args, **kwargs)
        cmd = kwargs.get("cmd", args[0] if args else [])
        if "-w" in cmd:
            directory.chmod(stat.S_IRWXU)
        return process

    return run


def test_permission_denied_then_clean_retry_stays_unmeasured(tmp_path: Path) -> None:
    """The failed write is not replaced by the retry that could have run."""
    directory = tmp_path / "ro"
    directory.mkdir()
    script = directory / "script.sh"
    script.write_text(_DIRTY)
    directory.chmod(stat.S_IRUSR | stat.S_IXUSR)
    plugin = ShfmtPlugin()
    try:
        with patch.object(
            plugin,
            "_run_subprocess_result",
            side_effect=_restore_after_first_write(plugin, directory),
        ):
            result = run_fix_with_retry(plugin, [str(script)], {}, 3)
    finally:
        directory.chmod(stat.S_IRWXU)

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.residual_unknown).is_true()
    assert_that(result.fixed_issues_count).is_none()
    assert_that(result.remaining_issues_count).is_none()
    assert_that(result.issues).is_not_empty()
    assert_that(result.output).contains("permission denied")
    assert_that(script.read_text()).is_equal_to(_DIRTY)


@pytest.mark.parametrize("name", ["ENOSPC.sh", "PermissionError.sh"])
def test_diagnostic_looking_name_is_fixed_and_measured(
    tmp_path: Path,
    name: str,
) -> None:
    """A clean ``shfmt -w`` is measured whatever the file is called."""
    script = tmp_path / name
    script.write_text(_DIRTY)

    result = run_fix_with_retry(ShfmtPlugin(), [str(script)], {}, 3)

    assert_that(result.success).is_true()
    assert_that(result.execution_failed).is_false()
    assert_that(result.residual_unknown).is_false()
    assert_that(result.fixed_issues_count).is_equal_to(result.initial_issues_count)
    assert_that(script.read_text()).is_not_equal_to(_DIRTY)
