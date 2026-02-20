"""Unit tests for BaseToolPlugin execution-related methods and ExecutionContext."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.plugins.base import (
    DEFAULT_EXCLUDE_PATTERNS,
    DEFAULT_TIMEOUT,
    ExecutionContext,
)

from .conftest import NoFixPlugin

if TYPE_CHECKING:
    from tests.unit.plugins.conftest import FakeToolPlugin


# =============================================================================
# ExecutionContext Tests
# =============================================================================


def test_execution_context_default_values() -> None:
    """Verify ExecutionContext initializes with expected default values."""
    ctx = ExecutionContext()

    assert_that(ctx.files).is_empty()
    assert_that(ctx.rel_files).is_empty()
    assert_that(ctx.cwd).is_none()
    assert_that(ctx.early_result).is_none()
    assert_that(ctx.timeout).is_equal_to(DEFAULT_TIMEOUT)


def test_execution_context_should_skip_false_when_no_early_result() -> None:
    """Verify should_skip is False when no early_result is set."""
    ctx = ExecutionContext()

    assert_that(ctx.should_skip).is_false()


def test_execution_context_should_skip_true_when_early_result_set() -> None:
    """Verify should_skip is True when early_result is set."""
    result = ToolResult(name="test", success=True, output="", issues_count=0)
    ctx = ExecutionContext(early_result=result)

    assert_that(ctx.should_skip).is_true()
    assert_that(ctx.early_result).is_instance_of(ToolResult)


# =============================================================================
# BaseToolPlugin.fix Tests
# =============================================================================


def test_fix_raises_not_implemented_when_can_fix_true_but_not_overridden(
    fake_tool_plugin: FakeToolPlugin,
) -> None:
    """Verify fix raises NotImplementedError when can_fix is True but not overridden.

    Args:
        fake_tool_plugin: The fake tool plugin instance to test.
    """
    with pytest.raises(NotImplementedError, match="Subclass must implement"):
        fake_tool_plugin.fix([], {})


def test_fix_raises_not_implemented_when_cannot_fix() -> None:
    """Verify fix raises NotImplementedError when can_fix is False."""
    plugin = NoFixPlugin()

    with pytest.raises(NotImplementedError, match="does not support fixing"):
        plugin.fix([], {})


# =============================================================================
# BaseToolPlugin._validate_paths Tests
# =============================================================================


def test_validate_paths_valid(fake_tool_plugin: FakeToolPlugin, tmp_path: Path) -> None:
    """Verify valid paths pass validation without raising.

    Args:
        fake_tool_plugin: Fixture providing a FakeToolPlugin instance.
        tmp_path: Pytest temporary directory fixture.
    """
    test_file = tmp_path / "test.py"
    test_file.write_text("print('hello')")

    fake_tool_plugin._validate_paths([str(test_file)])
    # Should not raise


def test_validate_paths_nonexistent_raises(fake_tool_plugin: FakeToolPlugin) -> None:
    """Verify nonexistent path raises FileNotFoundError.

    Args:
        fake_tool_plugin: Fixture providing a FakeToolPlugin instance.
    """
    with pytest.raises(FileNotFoundError, match="does not exist"):
        fake_tool_plugin._validate_paths(["/nonexistent/path"])


def test_validate_paths_inaccessible_raises(
    fake_tool_plugin: FakeToolPlugin,
    tmp_path: Path,
) -> None:
    """Verify inaccessible path raises PermissionError.

    Args:
        fake_tool_plugin: Fixture providing a FakeToolPlugin instance.
        tmp_path: Pytest temporary directory fixture.
    """
    test_file = tmp_path / "test.py"
    test_file.write_text("print('hello')")

    with patch("os.access", return_value=False):
        with pytest.raises(PermissionError, match="not accessible"):
            fake_tool_plugin._validate_paths([str(test_file)])


# =============================================================================
# BaseToolPlugin._get_cwd Tests
# =============================================================================


def test_get_cwd_single_file(fake_tool_plugin: FakeToolPlugin, tmp_path: Path) -> None:
    """Verify single file returns its parent directory as cwd.

    Args:
        fake_tool_plugin: Fixture providing a FakeToolPlugin instance.
        tmp_path: Pytest temporary directory fixture.
    """
    test_file = tmp_path / "test.py"
    test_file.write_text("")

    result = fake_tool_plugin._get_cwd([str(test_file)])

    assert_that(result).is_equal_to(str(tmp_path))


def test_get_cwd_multiple_files_same_directory(
    fake_tool_plugin: FakeToolPlugin,
    tmp_path: Path,
) -> None:
    """Verify multiple files in same directory return that directory as cwd.

    Args:
        fake_tool_plugin: Fixture providing a FakeToolPlugin instance.
        tmp_path: Pytest temporary directory fixture.
    """
    file1 = tmp_path / "a.py"
    file2 = tmp_path / "b.py"
    file1.write_text("")
    file2.write_text("")

    result = fake_tool_plugin._get_cwd([str(file1), str(file2)])

    assert_that(result).is_equal_to(str(tmp_path))


def test_get_cwd_multiple_files_different_directories(
    fake_tool_plugin: FakeToolPlugin,
    tmp_path: Path,
) -> None:
    """Verify multiple files in different directories return common parent as cwd.

    Args:
        fake_tool_plugin: Fixture providing a FakeToolPlugin instance.
        tmp_path: Pytest temporary directory fixture.
    """
    dir1 = tmp_path / "src"
    dir2 = tmp_path / "tests"
    dir1.mkdir()
    dir2.mkdir()
    file1 = dir1 / "a.py"
    file2 = dir2 / "b.py"
    file1.write_text("")
    file2.write_text("")

    result = fake_tool_plugin._get_cwd([str(file1), str(file2)])

    assert_that(result).is_equal_to(str(tmp_path))


def test_get_cwd_empty_paths_returns_none(fake_tool_plugin: FakeToolPlugin) -> None:
    """Verify empty paths list returns None.

    Args:
        fake_tool_plugin: Fixture providing a FakeToolPlugin instance.
    """
    result = fake_tool_plugin._get_cwd([])

    assert_that(result).is_none()


# =============================================================================
# BaseToolPlugin._prepare_execution Tests
# =============================================================================


def test_prepare_execution_version_check_fails_returns_early_result(
    fake_tool_plugin: FakeToolPlugin,
) -> None:
    """Verify early result is returned when version check fails.

    Args:
        fake_tool_plugin: Fixture providing a FakeToolPlugin instance.
    """
    skip_result = ToolResult(
        name="fake-tool",
        success=True,
        output="Version check failed",
        issues_count=0,
    )

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=skip_result,
    ):
        ctx = fake_tool_plugin._prepare_execution(["."], {})

        assert_that(ctx.should_skip).is_true()
        assert_that(ctx.early_result).is_equal_to(skip_result)
        assert_that(ctx.early_result).is_instance_of(ToolResult)


def test_prepare_execution_empty_paths_returns_early_result(
    fake_tool_plugin: FakeToolPlugin,
) -> None:
    """Verify early result is returned for empty paths.

    Args:
        fake_tool_plugin: Fixture providing a FakeToolPlugin instance.
    """
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        ctx = fake_tool_plugin._prepare_execution([], {})

        assert_that(ctx.should_skip).is_true()


def test_prepare_execution_no_files_found_returns_early_result(
    fake_tool_plugin: FakeToolPlugin,
    tmp_path: Path,
) -> None:
    """Verify early result is returned when no files are found.

    Args:
        fake_tool_plugin: Fixture providing a FakeToolPlugin instance.
        tmp_path: Pytest temporary directory fixture.
    """
    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch(
            "lintro.plugins.execution_preparation.discover_files",
            return_value=[],
        ),
    ):
        ctx = fake_tool_plugin._prepare_execution([str(tmp_path)], {})

        assert_that(ctx.should_skip).is_true()


def test_prepare_execution_successful_returns_context_with_files(
    fake_tool_plugin: FakeToolPlugin,
    tmp_path: Path,
) -> None:
    """Verify successful preparation returns context with discovered files.

    Args:
        fake_tool_plugin: Fixture providing a FakeToolPlugin instance.
        tmp_path: Pytest temporary directory fixture.
    """
    test_file = tmp_path / "test.py"
    test_file.write_text("print('hello')")

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch(
            "lintro.plugins.execution_preparation.discover_files",
            return_value=[str(test_file)],
        ),
    ):
        ctx = fake_tool_plugin._prepare_execution([str(tmp_path)], {})

        assert_that(ctx.should_skip).is_false()
        assert_that(ctx.files).is_length(1)
        assert_that(ctx.files).is_equal_to([str(test_file)])


# =============================================================================
# BaseToolPlugin._get_executable_command Tests
# =============================================================================


@pytest.mark.parametrize(
    ("tool_name", "expected_contains"),
    [
        pytest.param("ruff", ["-m", "ruff"], id="python_bundled_ruff"),
        pytest.param("pytest", ["-m", "pytest"], id="python_bundled_pytest"),
    ],
)
def test_get_executable_command_python_bundled_tools_fallback(
    fake_tool_plugin: FakeToolPlugin,
    tool_name: str,
    expected_contains: list[str],
) -> None:
    """Verify Python bundled tools fall back to python -m when not in PATH.

    Args:
        fake_tool_plugin: Fixture providing a FakeToolPlugin instance.
        tool_name: Name of the tool being tested.
        expected_contains: List of expected substrings in the command.
    """
    with (
        patch("shutil.which", return_value=None),
        patch(
            "lintro.tools.core.command_builders._is_compiled_binary",
            return_value=False,
        ),
    ):
        result = fake_tool_plugin._get_executable_command(tool_name)

    for item in expected_contains:
        assert_that(result).contains(item)


@pytest.mark.parametrize(
    "tool_name",
    [
        pytest.param("ruff", id="python_bundled_ruff"),
        pytest.param("pytest", id="python_bundled_pytest"),
    ],
)
def test_get_executable_command_python_bundled_tools_path_binary_outside_venv(
    fake_tool_plugin: FakeToolPlugin,
    tool_name: str,
) -> None:
    """Verify Python bundled tools prefer PATH binary when outside venv.

    Args:
        fake_tool_plugin: Fixture providing a FakeToolPlugin instance.
        tool_name: Name of the tool being tested.
    """
    expected_path = f"/usr/local/bin/{tool_name}"
    # Simulate running outside a venv (prefix == base_prefix)
    with (
        patch("shutil.which", return_value=expected_path),
        patch(
            "lintro.tools.core.command_builders.sys.prefix",
            "/usr/local",
        ),
        patch(
            "lintro.tools.core.command_builders.sys.base_prefix",
            "/usr/local",
        ),
        patch(
            "lintro.tools.core.command_builders._is_compiled_binary",
            return_value=False,
        ),
    ):
        result = fake_tool_plugin._get_executable_command(tool_name)

    assert_that(result).is_equal_to([expected_path])


@pytest.mark.parametrize(
    "tool_name",
    [
        pytest.param("ruff", id="python_bundled_ruff"),
        pytest.param("pytest", id="python_bundled_pytest"),
    ],
)
def test_get_executable_command_python_bundled_tools_python_module_in_venv(
    fake_tool_plugin: FakeToolPlugin,
    tool_name: str,
) -> None:
    """Verify Python bundled tools prefer python -m when inside venv.

    Args:
        fake_tool_plugin: Fixture providing a FakeToolPlugin instance.
        tool_name: Name of the tool being tested.
    """
    # Simulate running inside a venv (prefix != base_prefix)
    with (
        patch("shutil.which", return_value=f"/usr/local/bin/{tool_name}"),
        patch(
            "lintro.tools.core.command_builders.sys.prefix",
            "/app/.venv",
        ),
        patch(
            "lintro.tools.core.command_builders.sys.base_prefix",
            "/usr/local",
        ),
        patch(
            "lintro.tools.core.command_builders._is_compiled_binary",
            return_value=False,
        ),
    ):
        result = fake_tool_plugin._get_executable_command(tool_name)

    # Should return [python_exe, "-m", tool_name] even when PATH has tool
    assert_that(result).is_length(3)
    assert_that(result[1]).is_equal_to("-m")
    assert_that(result[2]).is_equal_to(tool_name)


def test_get_executable_command_nodejs_tool_with_bunx(
    fake_tool_plugin: FakeToolPlugin,
) -> None:
    """Verify Node.js tools return bunx command when bunx is available.

    Args:
        fake_tool_plugin: Fixture providing a FakeToolPlugin instance.
    """
    from lintro.enums.tool_name import ToolName

    with patch("shutil.which", return_value="/usr/bin/bunx"):
        result = fake_tool_plugin._get_executable_command(ToolName.MARKDOWNLINT)

        assert_that(result).contains("bunx", "markdownlint-cli2")


def test_get_executable_command_astro_check_with_bunx(
    fake_tool_plugin: FakeToolPlugin,
) -> None:
    """Verify astro-check resolves to bunx astro command."""
    with patch("shutil.which", return_value="/usr/bin/bunx"):
        result = fake_tool_plugin._get_executable_command("astro-check")

        assert_that(result).is_equal_to(["bunx", "astro"])


def test_get_executable_command_vue_tsc_with_bunx(
    fake_tool_plugin: FakeToolPlugin,
) -> None:
    """Verify vue-tsc resolves to bunx vue-tsc command."""
    with patch("shutil.which", return_value="/usr/bin/bunx"):
        result = fake_tool_plugin._get_executable_command("vue-tsc")

        assert_that(result).is_equal_to(["bunx", "vue-tsc"])


def test_get_executable_command_nodejs_tool_without_bunx(
    fake_tool_plugin: FakeToolPlugin,
) -> None:
    """Verify Node.js tools return tool name when bunx is not available.

    Args:
        fake_tool_plugin: Fixture providing a FakeToolPlugin instance.
    """
    from lintro.enums.tool_name import ToolName

    with patch("shutil.which", return_value=None):
        result = fake_tool_plugin._get_executable_command(ToolName.MARKDOWNLINT)

        assert_that(result).is_equal_to(["markdownlint-cli2"])


def test_get_executable_command_cargo_tool(fake_tool_plugin: FakeToolPlugin) -> None:
    """Verify Rust/Cargo tools return cargo command.

    Args:
        fake_tool_plugin: Fixture providing a FakeToolPlugin instance.
    """
    result = fake_tool_plugin._get_executable_command("clippy")

    assert_that(result).is_equal_to(["cargo", "clippy"])


def test_get_executable_command_unknown_tool(fake_tool_plugin: FakeToolPlugin) -> None:
    """Verify unknown tools return just the tool name.

    Args:
        fake_tool_plugin: Fixture providing a FakeToolPlugin instance.
    """
    result = fake_tool_plugin._get_executable_command("unknown-tool")

    assert_that(result).is_equal_to(["unknown-tool"])


# =============================================================================
# Module Constants Tests
# =============================================================================


@pytest.mark.parametrize(
    "pattern",
    [
        pytest.param(".git", id="git_directory"),
        pytest.param("__pycache__", id="pycache_directory"),
        pytest.param("*.pyc", id="pyc_files"),
    ],
)
def test_default_exclude_patterns_contains_expected_patterns(pattern: str) -> None:
    """Verify DEFAULT_EXCLUDE_PATTERNS contains essential patterns.

    Args:
        pattern: The pattern to check for in DEFAULT_EXCLUDE_PATTERNS.
    """
    assert_that(pattern in DEFAULT_EXCLUDE_PATTERNS).is_true()


def test_default_exclude_patterns_is_not_empty() -> None:
    """Verify DEFAULT_EXCLUDE_PATTERNS is not empty."""
    assert_that(DEFAULT_EXCLUDE_PATTERNS).is_not_empty()
