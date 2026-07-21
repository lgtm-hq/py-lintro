"""Unit tests for shellcheck plugin error handling and edge cases."""

from __future__ import annotations

import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.tools.definitions.shellcheck import ShellcheckPlugin
from tests.test_samples_helpers import copy_sample

# Tests for timeout handling


def test_check_with_timeout(
    shellcheck_plugin: ShellcheckPlugin,
    tmp_path: Path,
) -> None:
    """Check handles timeout correctly.

    Args:
        shellcheck_plugin: The ShellcheckPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = copy_sample(
        tmp_path,
        "tools",
        "shell",
        "shellcheck",
        "shellcheck_hello_world.sh",
        dest_name="test_script.sh",
    )

    with patch.object(
        shellcheck_plugin,
        "_run_subprocess",
        side_effect=subprocess.TimeoutExpired(cmd=["shellcheck"], timeout=30),
    ):
        result = shellcheck_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    # The timeout should be recorded in the output
    assert_that(result.output).contains("timeout")
