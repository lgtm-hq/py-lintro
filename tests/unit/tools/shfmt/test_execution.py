"""Unit tests for shfmt plugin execution."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

from assertpy import assert_that

from lintro.tools.definitions.shfmt import ShfmtPlugin

if TYPE_CHECKING:
    pass


# =============================================================================
# Tests for ShfmtPlugin.check method
# =============================================================================


def test_check_with_mocked_subprocess_success(
    shfmt_plugin: ShfmtPlugin,
    tmp_path: Path,
) -> None:
    """Check returns success when no issues found.

    Args:
        shfmt_plugin: The ShfmtPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test_script.sh"
    test_file.write_text('#!/bin/bash\necho "hello"\n')

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            shfmt_plugin,
            "_run_subprocess",
            return_value=(True, ""),
        ),
    ):
        result = shfmt_plugin.check([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_with_mocked_subprocess_issues(
    shfmt_plugin: ShfmtPlugin,
    tmp_path: Path,
) -> None:
    """Check returns issues when shfmt finds formatting problems.

    Args:
        shfmt_plugin: The ShfmtPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test_script.sh"
    test_file.write_text(
        '#!/bin/bash\nif [  "$foo" = "bar" ]; then\necho "match"\nfi\n',
    )

    shfmt_diff_output = f"""--- {test_file}
+++ {test_file}
@@ -1,4 +1,4 @@
 #!/bin/bash
-if [  "$foo" = "bar" ]; then
+if [ "$foo" = "bar" ]; then
 echo "match"
 fi"""

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            shfmt_plugin,
            "_run_subprocess",
            return_value=(False, shfmt_diff_output),
        ),
    ):
        result = shfmt_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)


def test_check_with_no_shell_files(
    shfmt_plugin: ShfmtPlugin,
    tmp_path: Path,
) -> None:
    """Check returns success when no shell files found.

    Args:
        shfmt_plugin: The ShfmtPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    non_sh_file = tmp_path / "test.txt"
    non_sh_file.write_text("Not a shell file")

    with patch.object(shfmt_plugin, "_verify_tool_version", return_value=None):
        result = shfmt_plugin.check([str(non_sh_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.output).contains("No")


# =============================================================================
# Tests for ShfmtPlugin.fix method
# =============================================================================


def test_fix_with_mocked_subprocess_success(
    shfmt_plugin: ShfmtPlugin,
    tmp_path: Path,
) -> None:
    """Fix returns success when fixes are applied.

    Args:
        shfmt_plugin: The ShfmtPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test_script.sh"
    test_file.write_text(
        '#!/bin/bash\nif [  "$foo" = "bar" ]; then\necho "match"\nfi\n',
    )

    shfmt_diff_output = f"""--- {test_file}
+++ {test_file}
@@ -1,4 +1,4 @@
 #!/bin/bash
-if [  "$foo" = "bar" ]; then
+if [ "$foo" = "bar" ]; then
 echo "match"
 fi"""

    call_count = 0

    def mock_run_subprocess(
        cmd: list[str],
        timeout: int,
        cwd: str | None = None,
    ) -> tuple[bool, str]:
        """Mock subprocess that returns diff on check, success on fix.

        Args:
            cmd: Command list.
            timeout: Timeout in seconds.
            cwd: Working directory.

        Returns:
            Tuple of (success, output).
        """
        nonlocal call_count
        call_count += 1
        # First call is check with -d flag
        if "-d" in cmd:
            return (False, shfmt_diff_output)
        # Second call is fix with -w flag
        return (True, "")

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            shfmt_plugin,
            "_run_subprocess",
            side_effect=mock_run_subprocess,
        ),
    ):
        result = shfmt_plugin.fix([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.fixed_issues_count).is_greater_than(0)
    # Verify the mock was called expected number of times (check + fix)
    assert_that(call_count).is_equal_to(2)


def test_fix_with_nothing_to_fix(
    shfmt_plugin: ShfmtPlugin,
    tmp_path: Path,
) -> None:
    """Fix returns success when no fixes needed.

    Args:
        shfmt_plugin: The ShfmtPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test_script.sh"
    test_file.write_text('#!/bin/bash\necho "hello"\n')

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            shfmt_plugin,
            "_run_subprocess",
            return_value=(True, ""),
        ),
    ):
        result = shfmt_plugin.fix([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.output).contains("No fixes needed")
