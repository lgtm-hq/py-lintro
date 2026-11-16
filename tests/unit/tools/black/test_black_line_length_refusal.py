"""Black's Ruff E501 supplement validates its launch (#3486).

The supplement runs Ruff on repository file names. When Ruff resolves to a
batch shim, a file name holding a ``cmd.exe`` metacharacter must be refused
before Ruff starts, recorded on the plugin, and reported as a failed
supplement rather than a clean "no violations" result.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from assertpy import assert_that

from lintro.exceptions.errors import UnsafeCommandError
from lintro.tools.black.definition import BlackPlugin


def test_batch_ruff_supplement_never_launches_with_a_metacharacter_file_name(
    tmp_path: Path,
) -> None:
    """A file named ``a&whoami&b.py`` never reaches a ``ruff.cmd`` launch.

    Args:
        tmp_path: Temporary directory used as the working directory.
    """
    launched: list[list[str]] = []

    def _record(cmd: list[str], **_kwargs: Any) -> None:
        launched.append(list(cmd))
        raise AssertionError("ruff must not be launched")

    plugin = BlackPlugin()
    with (
        patch(
            "lintro.tools.core.line_length_checker.shutil.which",
            return_value="C:/tools/ruff.cmd",
        ),
        patch(
            "lintro.tools.core.line_length_checker.subprocess.run",
            side_effect=_record,
        ),
    ):
        outcome = plugin._line_length_result(["a&whoami&b.py"], str(tmp_path))

    assert_that(launched).is_empty()
    assert_that(outcome.failed).is_true()
    assert_that(outcome.issues).is_empty()
    assert_that(outcome.note).contains("E501 supplement failed")
    assert_that(plugin.command_refusal).is_instance_of(UnsafeCommandError)
    assert_that(str(plugin.command_refusal)).contains("a&whoami&b.py")
