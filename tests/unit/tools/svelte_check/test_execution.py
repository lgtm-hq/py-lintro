"""Unit tests for svelte-check plugin check method execution."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.parsers.svelte_check.svelte_check_issue import SvelteCheckIssue
from lintro.tools.definitions.svelte_check import SvelteCheckPlugin


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
        "src/lib/Button.svelte:15:5:15:10 Error "
        "Type 'string' is not assignable to type 'number'.\n"
        "src/routes/+page.svelte:20:3:20:15 Error "
        "Property 'foo' does not exist on type 'Props'."
    )
    return (False, output)


def test_check_no_svelte_files(
    svelte_check_plugin: SvelteCheckPlugin,
    tmp_path: Path,
) -> None:
    """Check returns early when no Svelte files found.

    Args:
        svelte_check_plugin: The SvelteCheckPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    # Create a non-Svelte file
    test_file = tmp_path / "test.ts"
    test_file.write_text("const x = 1;")

    result = svelte_check_plugin.check([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).contains("No .svelte files found to check.")


def test_check_no_svelte_config_proceeds_with_defaults(
    svelte_check_plugin: SvelteCheckPlugin,
    tmp_path: Path,
) -> None:
    """Check proceeds with defaults when no Svelte config found.

    Args:
        svelte_check_plugin: The SvelteCheckPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    # Create a Svelte file but no config
    svelte_file = tmp_path / "test.svelte"
    svelte_file.write_text("<script>\nlet count = 0;\n</script>\n<h1>{count}</h1>")

    with patch.object(
        svelte_check_plugin,
        "_run_subprocess",
        side_effect=_mock_subprocess_success,
    ):
        result = svelte_check_plugin.check([str(tmp_path)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_with_mocked_subprocess_success(
    svelte_check_plugin: SvelteCheckPlugin,
    tmp_path: Path,
) -> None:
    """Check returns success when svelte-check finds no issues.

    Args:
        svelte_check_plugin: The SvelteCheckPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    # Create Svelte file and config
    svelte_file = tmp_path / "test.svelte"
    svelte_file.write_text("<script>\nlet count = 0;\n</script>\n<h1>{count}</h1>")
    config_file = tmp_path / "svelte.config.js"
    config_file.write_text("export default {};")

    with patch.object(
        svelte_check_plugin,
        "_run_subprocess",
        side_effect=_mock_subprocess_success,
    ):
        result = svelte_check_plugin.check([str(tmp_path)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_with_mocked_subprocess_issues_found(
    svelte_check_plugin: SvelteCheckPlugin,
    tmp_path: Path,
) -> None:
    """Check returns issues when svelte-check finds type errors.

    Args:
        svelte_check_plugin: The SvelteCheckPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    # Create Svelte file and config
    svelte_file = tmp_path / "index.svelte"
    svelte_file.write_text(
        "<script lang='ts'>\nlet x: number = 'bad';\n</script>\n<h1>{x}</h1>",
    )
    config_file = tmp_path / "svelte.config.js"
    config_file.write_text("export default {};")

    with patch.object(
        svelte_check_plugin,
        "_run_subprocess",
        side_effect=_mock_subprocess_with_issues,
    ):
        result = svelte_check_plugin.check([str(tmp_path)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(2)
    issues = result.issues
    assert_that(issues).is_not_none()
    assert issues is not None  # narrow type for mypy
    assert_that(issues).is_length(2)

    # Verify first issue
    first_issue = issues[0]
    assert_that(first_issue).is_instance_of(SvelteCheckIssue)
    assert isinstance(first_issue, SvelteCheckIssue)  # narrow type for mypy
    assert_that(first_issue.file).is_equal_to("src/lib/Button.svelte")
    assert_that(first_issue.line).is_equal_to(15)
    assert_that(first_issue.severity).is_equal_to("error")


def test_fix_raises_not_implemented(
    svelte_check_plugin: SvelteCheckPlugin,
    tmp_path: Path,
) -> None:
    """Fix method raises NotImplementedError.

    Args:
        svelte_check_plugin: The SvelteCheckPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    with pytest.raises(NotImplementedError, match="cannot automatically fix"):
        svelte_check_plugin.fix([str(tmp_path)], {})


def test_check_with_threshold_option(
    svelte_check_plugin: SvelteCheckPlugin,
    tmp_path: Path,
) -> None:
    """Check uses threshold option when provided.

    Args:
        svelte_check_plugin: The SvelteCheckPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    # Create Svelte file and config
    svelte_file = tmp_path / "test.svelte"
    svelte_file.write_text("<script>\nlet count = 0;\n</script>\n<h1>{count}</h1>")
    config_file = tmp_path / "svelte.config.js"
    config_file.write_text("export default {};")

    captured_cmd: list[str] = []

    def capture_cmd(cmd: list[str], **kwargs: Any) -> tuple[bool, str]:
        captured_cmd.extend(cmd)
        return (True, "")

    with patch.object(
        svelte_check_plugin,
        "_run_subprocess",
        side_effect=capture_cmd,
    ):
        svelte_check_plugin.check(
            [str(tmp_path)],
            {"threshold": "warning"},
        )

    # Verify --threshold was passed with the correct value
    assert_that(captured_cmd).contains("--threshold")
    threshold_idx = captured_cmd.index("--threshold")
    assert_that(captured_cmd[threshold_idx + 1]).is_equal_to("warning")
