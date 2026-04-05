"""Tests for ShfmtPlugin.fix method initial_issues population."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.tools.definitions.shfmt import ShfmtPlugin


def test_fix_populates_initial_issues(
    shfmt_plugin: ShfmtPlugin,
    tmp_path: Path,
) -> None:
    """Fix populates initial_issues when issues are found and fixed.

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
        if "-d" in cmd:
            return (False, shfmt_diff_output)
        return (True, "")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            shfmt_plugin,
            "_run_subprocess",
            side_effect=mock_run_subprocess,
        ):
            result = shfmt_plugin.fix([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.initial_issues).is_not_none()
    assert_that(result.initial_issues).is_not_empty()
    assert_that(result.initial_issues_count).is_greater_than(0)
    assert_that(result.fixed_issues_count).is_greater_than(0)
    assert_that(result.remaining_issues_count).is_equal_to(0)


def test_fix_initial_issues_none_when_no_issues(
    shfmt_plugin: ShfmtPlugin,
    tmp_path: Path,
) -> None:
    """Fix sets initial_issues to None when no issues detected.

    Args:
        shfmt_plugin: The ShfmtPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test_script.sh"
    test_file.write_text('#!/bin/bash\necho "hello"\n')

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            shfmt_plugin,
            "_run_subprocess",
            return_value=(True, ""),
        ):
            result = shfmt_plugin.fix([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.initial_issues).is_none()
