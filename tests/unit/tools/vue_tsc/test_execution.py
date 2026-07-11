"""Unit tests for vue-tsc plugin check method execution."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.parsers.vue_tsc.vue_tsc_issue import VueTscIssue
from lintro.tools.definitions.vue_tsc import VueTscPlugin


def _mock_subprocess_success(**kwargs: Any) -> tuple[bool, str]:
    """Mock subprocess that returns success with no output.

    Args:
        **kwargs: Ignored keyword arguments.

    Returns:
        Tuple of (success=True, empty string).
    """
    return (True, "")


def _mock_subprocess_with_issues(**kwargs: Any) -> tuple[bool, str]:
    """Mock subprocess that returns output with type errors.

    Args:
        **kwargs: Ignored keyword arguments.

    Returns:
        Tuple of (success=False, error output).
    """
    output = (
        "src/components/Button.vue(10,5): error TS2322: "
        "Type 'string' is not assignable to type 'number'.\n"
        "src/components/Card.vue(15,10): error TS2339: "
        "Property 'foo' does not exist on type 'Props'."
    )
    return (False, output)


def test_check_no_vue_files(
    vue_tsc_plugin: VueTscPlugin,
    tmp_path: Path,
) -> None:
    """Check returns early when no Vue files found.

    Args:
        vue_tsc_plugin: The VueTscPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    # Create a non-Vue file
    test_file = tmp_path / "test.ts"
    test_file.write_text("const x = 1;")

    result = vue_tsc_plugin.check([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_with_mocked_subprocess_success(
    vue_tsc_plugin: VueTscPlugin,
    tmp_path: Path,
) -> None:
    """Check returns success when vue-tsc finds no issues.

    Args:
        vue_tsc_plugin: The VueTscPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    # Create Vue file and tsconfig
    vue_file = tmp_path / "test.vue"
    vue_file.write_text("<template><div>Hello</div></template>")
    tsconfig = tmp_path / "tsconfig.json"
    tsconfig.write_text('{"compilerOptions": {}}')

    with patch.object(
        vue_tsc_plugin,
        "_run_subprocess",
        side_effect=_mock_subprocess_success,
    ):
        result = vue_tsc_plugin.check([str(tmp_path)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_with_mocked_subprocess_issues_found(
    vue_tsc_plugin: VueTscPlugin,
    tmp_path: Path,
) -> None:
    """Check returns issues when vue-tsc finds type errors.

    Args:
        vue_tsc_plugin: The VueTscPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    # Create Vue file and tsconfig
    vue_file = tmp_path / "Button.vue"
    vue_file.write_text("<template><div>Hello</div></template>")
    tsconfig = tmp_path / "tsconfig.json"
    tsconfig.write_text('{"compilerOptions": {}}')

    with patch.object(
        vue_tsc_plugin,
        "_run_subprocess",
        side_effect=_mock_subprocess_with_issues,
    ):
        result = vue_tsc_plugin.check([str(tmp_path)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(2)
    issues = result.issues
    assert_that(issues).is_not_none()
    assert issues is not None
    assert_that(issues).is_length(2)

    # Verify first issue
    first_issue = issues[0]
    assert isinstance(first_issue, VueTscIssue)
    assert_that(first_issue.file).is_equal_to("src/components/Button.vue")
    assert_that(first_issue.line).is_equal_to(10)
    assert_that(first_issue.code).is_equal_to("TS2322")


def test_fix_raises_not_implemented(
    vue_tsc_plugin: VueTscPlugin,
    tmp_path: Path,
) -> None:
    """Fix method raises NotImplementedError.

    Args:
        vue_tsc_plugin: The VueTscPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    with pytest.raises(NotImplementedError, match="cannot automatically fix"):
        vue_tsc_plugin.fix([str(tmp_path)], {})


def test_check_with_project_option(
    vue_tsc_plugin: VueTscPlugin,
    tmp_path: Path,
) -> None:
    """Check uses project option when provided.

    Args:
        vue_tsc_plugin: The VueTscPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    # Create Vue file and tsconfig
    vue_file = tmp_path / "test.vue"
    vue_file.write_text("<template><div>Hello</div></template>")
    tsconfig = tmp_path / "tsconfig.app.json"
    tsconfig.write_text('{"compilerOptions": {}}')

    captured_cmd: list[str] = []

    def capture_cmd(cmd: list[str], **kwargs: Any) -> tuple[bool, str]:
        captured_cmd.extend(cmd)
        return (True, "")

    with patch.object(
        vue_tsc_plugin,
        "_run_subprocess",
        side_effect=capture_cmd,
    ):
        vue_tsc_plugin.check(
            [str(tmp_path)],
            {"project": str(tsconfig)},
        )

    # Verify --project was passed
    assert_that(captured_cmd).contains("--project")
    project_idx = captured_cmd.index("--project")
    assert_that(captured_cmd[project_idx + 1]).is_equal_to(str(tsconfig))


def test_check_no_tsconfig_passes_files_directly(
    vue_tsc_plugin: VueTscPlugin,
    tmp_path: Path,
) -> None:
    """Check passes files directly when no tsconfig.json is found.

    Args:
        vue_tsc_plugin: The VueTscPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    # Create Vue file but NO tsconfig.json
    vue_file = tmp_path / "App.vue"
    vue_file.write_text("<template><div>Hello</div></template>")

    captured_cmd: list[str] = []

    def capture_cmd(cmd: list[str], **kwargs: Any) -> tuple[bool, str]:
        captured_cmd.extend(cmd)
        return (True, "")

    with patch.object(
        vue_tsc_plugin,
        "_run_subprocess",
        side_effect=capture_cmd,
    ):
        result = vue_tsc_plugin.check([str(tmp_path)], {})

    assert_that(result.success).is_true()
    # No --project flag should be present
    assert_that(captured_cmd).does_not_contain("--project")
    # File should be passed directly in the command
    assert_that(" ".join(captured_cmd)).contains("App.vue")


def test_check_timeout_handling(
    vue_tsc_plugin: VueTscPlugin,
    tmp_path: Path,
) -> None:
    """Check handles subprocess timeout gracefully.

    Args:
        vue_tsc_plugin: The VueTscPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False

    # Create Vue file and tsconfig
    vue_file = tmp_path / "test.vue"
    vue_file.write_text("<template><div>Hello</div></template>")
    tsconfig = tmp_path / "tsconfig.json"
    tsconfig.write_text('{"compilerOptions": {}}')

    with patch.object(
        vue_tsc_plugin,
        "_run_subprocess",
        side_effect=subprocess.TimeoutExpired("vue-tsc", 120),
    ):
        result = vue_tsc_plugin.check([str(tmp_path)], {})

    assert_that(result.success).is_false()
    assert_that(result.output).contains("timeout")
