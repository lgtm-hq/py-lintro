"""Unit tests for shellcheck plugin error handling and edge cases."""

from __future__ import annotations

import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.tools.definitions.shellcheck import ShellcheckPlugin

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
    test_file = tmp_path / "test_script.sh"
    test_file.write_text('#!/bin/bash\necho "Hello"\n')

    with patch.object(
        shellcheck_plugin,
        "_run_subprocess",
        side_effect=subprocess.TimeoutExpired(cmd=["shellcheck"], timeout=30),
    ):
        result = shellcheck_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    # The timeout should be recorded in the output
    assert_that(result.output).contains("timeout")
