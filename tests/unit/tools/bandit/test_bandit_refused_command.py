"""Bandit's direct launch goes through command validation (#3486).

Bandit runs ``subprocess.run`` itself to keep stdout and stderr apart, and
its argv carries repository file names. When Bandit resolves to a batch shim,
a file name holding a ``cmd.exe`` metacharacter must be refused before the
process starts, and the refusal must reach the executor as an execution
failure.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from assertpy import assert_that

from lintro.exceptions.errors import UnsafeCommandError
from lintro.tools.bandit.definition import BanditPlugin
from lintro.utils.execution.result_shaping import mark_refused_command


def test_batch_bandit_never_launches_with_a_metacharacter_file_name(
    tmp_path: Path,
) -> None:
    """A file named ``a&whoami&b.py`` never reaches a ``bandit.cmd`` launch.

    Args:
        tmp_path: Temporary directory for a real Python scan target.
    """
    py_file = tmp_path / "mod.py"
    py_file.write_text("x = 1\n")
    launched: list[list[str]] = []

    def _record(cmd: list[str], **_kwargs: Any) -> None:
        launched.append(list(cmd))
        raise AssertionError("bandit must not be launched")

    plugin = BanditPlugin()
    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            BanditPlugin,
            "_build_check_command",
            return_value=["C:/tools/bandit.cmd", "-f", "json", "C:/repo/a&whoami&b.py"],
        ),
        patch("lintro.tools.bandit.definition.subprocess.run", side_effect=_record),
    ):
        result = plugin.check([str(py_file)], {})

    assert_that(launched).is_empty()
    assert_that(plugin.command_refusal).is_instance_of(UnsafeCommandError)
    assert_that(str(plugin.command_refusal)).contains("a&whoami&b.py")
    assert_that(result.success).is_false()

    marked = mark_refused_command(plugin, result, fix=False)
    assert_that(marked.execution_failed).is_true()
    assert_that(marked.findings_complete).is_false()
    assert_that(marked.output).contains("lintro refused to run bandit")
