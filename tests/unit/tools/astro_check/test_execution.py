"""Unit tests for astro-check plugin check method execution."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.parsers.astro_check.astro_check_issue import AstroCheckIssue
from lintro.tools.definitions.astro_check import AstroCheckPlugin


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
        "src/pages/index.astro:10:5 - error ts2322: "
        "Type 'string' is not assignable to type 'number'.\n"
        "src/components/Card.astro:15:10 - error ts2339: "
        "Property 'foo' does not exist on type 'Props'."
    )
    return (False, output)


def test_check_no_astro_files(
    astro_check_plugin: AstroCheckPlugin,
    tmp_path: Path,
) -> None:
    """Check returns early when no Astro files found.

    Args:
        astro_check_plugin: The AstroCheckPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    # Create a non-Astro file
    test_file = tmp_path / "test.ts"
    test_file.write_text("const x = 1;")

    result = astro_check_plugin.check([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).contains("No Astro files to check.")


def test_check_no_astro_config_proceeds_with_defaults(
    astro_check_plugin: AstroCheckPlugin,
    tmp_path: Path,
) -> None:
    """Check proceeds with defaults when no Astro config found.

    Args:
        astro_check_plugin: The AstroCheckPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    # Create an Astro file but no config
    astro_file = tmp_path / "test.astro"
    astro_file.write_text("---\nconst message = 'Hello';\n---\n<h1>{message}</h1>")

    with patch.object(
        astro_check_plugin,
        "_run_subprocess",
        side_effect=_mock_subprocess_success,
    ) as mock_run:
        result = astro_check_plugin.check([str(tmp_path)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    mock_run.assert_called_once()
    _, kwargs = mock_run.call_args
    assert_that(kwargs["cwd"]).is_equal_to(str(tmp_path))
    assert_that(kwargs["cmd"]).contains("check")


def test_check_with_mocked_subprocess_success(
    astro_check_plugin: AstroCheckPlugin,
    tmp_path: Path,
) -> None:
    """Check returns success when astro check finds no issues.

    Args:
        astro_check_plugin: The AstroCheckPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    # Create Astro file and config
    astro_file = tmp_path / "test.astro"
    astro_file.write_text("---\nconst message = 'Hello';\n---\n<h1>{message}</h1>")
    config_file = tmp_path / "astro.config.mjs"
    config_file.write_text("export default {};")

    with patch.object(
        astro_check_plugin,
        "_run_subprocess",
        side_effect=_mock_subprocess_success,
    ):
        result = astro_check_plugin.check([str(tmp_path)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_with_mocked_subprocess_issues_found(
    astro_check_plugin: AstroCheckPlugin,
    tmp_path: Path,
) -> None:
    """Check returns issues when astro check finds type errors.

    Args:
        astro_check_plugin: The AstroCheckPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    # Create Astro file and config in the same directory
    # (plugin searches for config in cwd, which is the file's directory)
    astro_file = tmp_path / "index.astro"
    astro_file.write_text("---\nconst x: number = 'bad';\n---\n<h1>{x}</h1>")
    config_file = tmp_path / "astro.config.mjs"
    config_file.write_text("export default {};")

    with patch.object(
        astro_check_plugin,
        "_run_subprocess",
        side_effect=_mock_subprocess_with_issues,
    ):
        result = astro_check_plugin.check([str(tmp_path)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(2)
    issues = result.issues
    assert_that(issues).is_not_none()
    assert issues is not None  # narrow type for mypy
    assert_that(issues).is_length(2)

    # Verify first issue
    first_issue = issues[0]
    assert_that(first_issue).is_instance_of(AstroCheckIssue)
    assert isinstance(first_issue, AstroCheckIssue)  # narrow type for mypy
    assert_that(first_issue.file).is_equal_to("src/pages/index.astro")
    assert_that(first_issue.line).is_equal_to(10)
    assert_that(first_issue.code).is_equal_to("TS2322")


def test_fix_raises_not_implemented(
    astro_check_plugin: AstroCheckPlugin,
    tmp_path: Path,
) -> None:
    """Fix method raises NotImplementedError.

    Args:
        astro_check_plugin: The AstroCheckPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    with pytest.raises(NotImplementedError, match="cannot automatically fix"):
        astro_check_plugin.fix([str(tmp_path)], {})


def test_check_with_root_option(
    astro_check_plugin: AstroCheckPlugin,
    tmp_path: Path,
) -> None:
    """Check uses root option when provided.

    Args:
        astro_check_plugin: The AstroCheckPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    # Create Astro file and config in a subdirectory
    project_dir = tmp_path / "packages" / "web"
    project_dir.mkdir(parents=True)
    astro_file = project_dir / "test.astro"
    astro_file.write_text("---\nconst message = 'Hello';\n---\n<h1>{message}</h1>")
    config_file = project_dir / "astro.config.mjs"
    config_file.write_text("export default {};")

    captured_cmd: list[str] = []

    def capture_cmd(cmd: list[str], **kwargs: Any) -> tuple[bool, str]:
        captured_cmd.extend(cmd)
        return (True, "")

    with patch.object(
        astro_check_plugin,
        "_run_subprocess",
        side_effect=capture_cmd,
    ):
        astro_check_plugin.check(
            [str(tmp_path)],
            {"root": str(project_dir)},
        )

    # Verify --root was passed with the correct project directory path
    assert_that(captured_cmd).contains("--root")
    root_idx = captured_cmd.index("--root")
    assert_that(captured_cmd[root_idx + 1]).is_equal_to(str(project_dir))
