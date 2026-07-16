"""Parametrized tests for common tool plugin behaviors.

This module consolidates duplicate tests from individual tool test files,
following DRY principles. Tests here cover common patterns that all tools share.
"""

from __future__ import annotations

import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.enums.tool_name import ToolName
from lintro.plugins.base import BaseToolPlugin

if TYPE_CHECKING:
    from collections.abc import Callable


# =============================================================================
# Helper function for dynamic plugin instantiation
# =============================================================================


def _get_plugin_instance(plugin_class_path: str) -> BaseToolPlugin:
    """Dynamically import and instantiate a plugin class.

    Args:
        plugin_class_path: Full module path to the plugin class.

    Returns:
        An instance of the plugin class.
    """
    module_path, class_name = plugin_class_path.rsplit(".", 1)
    import importlib

    # Safe: module_path comes from hardcoded test constants, not user input
    module = importlib.import_module(module_path)  # nosemgrep: non-literal-import
    plugin_class = getattr(module, class_name)

    return cast(BaseToolPlugin, plugin_class())


# =============================================================================
# Test Data: Tools that support check operation
# =============================================================================

# Tools with their check success configurations
# (plugin_class_path, tool_name, sample_file, success_output)
TOOL_CHECK_SUCCESS_CONFIGS = [
    pytest.param(
        "lintro.tools.definitions.black.BlackPlugin",
        ToolName.BLACK,
        "test.py",
        (True, "All done! 1 file left unchanged."),
        id="black",
    ),
]

# Tools with their check failure configurations
# (plugin_class_path, tool_name, sample_file, failure_output)
TOOL_CHECK_FAILURE_CONFIGS = [
    pytest.param(
        "lintro.tools.definitions.black.BlackPlugin",
        ToolName.BLACK,
        "test.py",
        (False, "would reformat test.py\nOh no! 1 file would be reformatted."),
        id="black",
    ),
]

# Tools with their timeout configurations
# (plugin_class_path, tool_name, executable_cmd)
TOOL_TIMEOUT_CONFIGS = [
    pytest.param(
        "lintro.tools.definitions.black.BlackPlugin",
        ToolName.BLACK,
        ["black"],
        id="black",
    ),
]

# Tools that cannot fix issues (raise NotImplementedError)
# (plugin_class_path, error_match_pattern)
TOOLS_THAT_CANNOT_FIX = [
    pytest.param(
        "lintro.tools.definitions.hadolint.HadolintPlugin",
        "cannot automatically fix",
        id="hadolint",
    ),
    pytest.param(
        "lintro.tools.definitions.yamllint.YamllintPlugin",
        "cannot automatically fix",
        id="yamllint",
    ),
    pytest.param(
        "lintro.tools.definitions.markdownlint.MarkdownlintPlugin",
        "cannot fix issues",
        id="markdownlint",
    ),
    pytest.param(
        "lintro.tools.definitions.mypy.MypyPlugin",
        "cannot automatically fix",
        id="mypy",
    ),
    pytest.param(
        "lintro.tools.definitions.pytest.PytestPlugin",
        "cannot automatically fix",
        id="pytest",
    ),
]

# Tools with early skip behavior
# (plugin_class_path, tool_name)
TOOL_EARLY_SKIP_CONFIGS = [
    pytest.param(
        "lintro.tools.definitions.black.BlackPlugin",
        ToolName.BLACK,
        id="black",
    ),
    pytest.param(
        "lintro.tools.definitions.hadolint.HadolintPlugin",
        ToolName.HADOLINT,
        id="hadolint",
    ),
]


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_execution_context() -> Callable[..., MagicMock]:
    """Factory for creating mock ExecutionContext instances.

    Returns:
        A factory function that creates configured MagicMock objects.
    """

    def _create(
        files: list[str] | None = None,
        rel_files: list[str] | None = None,
        cwd: str = "/tmp",
        timeout: int = 30,
        should_skip: bool = False,
        early_result: Any = None,
    ) -> MagicMock:
        ctx = MagicMock()
        ctx.files = files or []
        ctx.rel_files = rel_files or []
        ctx.cwd = cwd
        ctx.timeout = timeout
        ctx.should_skip = should_skip
        ctx.early_result = early_result
        return ctx

    return _create


# =============================================================================
# Test: Check Success (No Issues)
# =============================================================================


@pytest.mark.parametrize(
    ("plugin_class_path", "expected_name", "sample_file", "subprocess_result"),
    TOOL_CHECK_SUCCESS_CONFIGS,
)
def test_check_success_no_issues(
    plugin_class_path: str,
    expected_name: ToolName,
    sample_file: str,
    subprocess_result: tuple[bool, str],
    mock_execution_context: Callable[..., MagicMock],
) -> None:
    """Check returns success when no issues found.

    This test is parametrized across multiple tools to verify the common
    behavior pattern of returning success with zero issues.

    Args:
        plugin_class_path: Full module path to the plugin class.
        expected_name: The expected tool name.
        sample_file: Sample file for testing.
        subprocess_result: Mock result tuple (success, output).
        mock_execution_context: Factory for mock execution contexts.
    """
    plugin = _get_plugin_instance(plugin_class_path)
    ctx = mock_execution_context(
        files=[sample_file],
        rel_files=[sample_file],
    )

    with (
        patch.object(plugin, "_prepare_execution", return_value=ctx),
        patch.object(plugin, "_run_subprocess", return_value=subprocess_result),
        patch.object(
            plugin,
            "_get_executable_command",
            return_value=[str(expected_name).lower()],
        ),
    ):
        # Handle Black's extra methods
        if hasattr(plugin, "_build_common_args"):
            with patch.object(plugin, "_build_common_args", return_value=[]):
                if hasattr(plugin, "_check_line_length_violations"):
                    with patch.object(
                        plugin,
                        "_check_line_length_violations",
                        return_value=[],
                    ):
                        result = plugin.check([f"/tmp/{sample_file}"], {})
                else:
                    result = plugin.check([f"/tmp/{sample_file}"], {})
        else:
            result = plugin.check([f"/tmp/{sample_file}"], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.name).is_equal_to(expected_name)


# =============================================================================
# Test: Check Failure (With Issues)
# =============================================================================


@pytest.mark.parametrize(
    ("plugin_class_path", "expected_name", "sample_file", "subprocess_result"),
    TOOL_CHECK_FAILURE_CONFIGS,
)
def test_check_failure_with_issues(
    plugin_class_path: str,
    expected_name: ToolName,
    sample_file: str,
    subprocess_result: tuple[bool, str],
    mock_execution_context: Callable[..., MagicMock],
) -> None:
    """Check returns failure when issues found.

    This test is parametrized across multiple tools to verify the common
    behavior pattern of returning failure when issues are detected.

    Args:
        plugin_class_path: Full module path to the plugin class.
        expected_name: The expected tool name.
        sample_file: Sample file for testing.
        subprocess_result: Mock result tuple (success, output).
        mock_execution_context: Factory for mock execution contexts.
    """
    plugin = _get_plugin_instance(plugin_class_path)
    ctx = mock_execution_context(
        files=[sample_file],
        rel_files=[sample_file],
    )

    with (
        patch.object(plugin, "_prepare_execution", return_value=ctx),
        patch.object(plugin, "_run_subprocess", return_value=subprocess_result),
        patch.object(
            plugin,
            "_get_executable_command",
            return_value=[str(expected_name).lower()],
        ),
    ):
        # Handle tool-specific extra methods
        if hasattr(plugin, "_build_common_args"):
            with patch.object(plugin, "_build_common_args", return_value=[]):
                if hasattr(plugin, "_check_line_length_violations"):
                    with patch.object(
                        plugin,
                        "_check_line_length_violations",
                        return_value=[],
                    ):
                        result = plugin.check([f"/tmp/{sample_file}"], {})
                else:
                    result = plugin.check([f"/tmp/{sample_file}"], {})
        elif hasattr(plugin, "_build_config_args"):
            with patch.object(plugin, "_build_config_args", return_value=[]):
                result = plugin.check([f"/tmp/{sample_file}"], {})
        else:
            result = plugin.check([f"/tmp/{sample_file}"], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)


# =============================================================================
# Test: Check Timeout Handling
# =============================================================================


def _create_mock_timeout_result(tool_name: str) -> MagicMock:
    """Create a mock timeout result for testing.

    Args:
        tool_name: Name of the tool.

    Returns:
        A mock ToolResult representing a timeout.
    """
    result = MagicMock()
    result.success = False
    result.output = f"{tool_name} execution timed out (30s limit exceeded)."
    result.issues_count = 1
    return result


@pytest.mark.parametrize(
    ("plugin_class_path", "expected_name", "executable_cmd"),
    TOOL_TIMEOUT_CONFIGS,
)
def test_check_timeout_handling(
    plugin_class_path: str,
    expected_name: ToolName,
    executable_cmd: list[str],
    mock_execution_context: Callable[..., MagicMock],
) -> None:
    """Check handles timeout correctly across tools.

    This test is parametrized across multiple tools to verify that
    timeout exceptions are properly caught and handled.

    Args:
        plugin_class_path: Full module path to the plugin class.
        expected_name: The expected tool name.
        executable_cmd: The executable command list.
        mock_execution_context: Factory for mock execution contexts.
    """
    plugin = _get_plugin_instance(plugin_class_path)
    ctx = mock_execution_context(
        files=["test_file"],
        rel_files=["test_file"],
    )

    timeout_result = _create_mock_timeout_result(str(expected_name).lower())

    with (
        patch.object(plugin, "_prepare_execution", return_value=ctx),
        patch.object(
            plugin,
            "_run_subprocess",
            side_effect=subprocess.TimeoutExpired(cmd=executable_cmd, timeout=30),
        ),
        patch.object(plugin, "_get_executable_command", return_value=executable_cmd),
    ):
        # Handle tool-specific extra methods and timeout result methods
        extra_patches = []

        if hasattr(plugin, "_build_common_args"):
            extra_patches.append(
                patch.object(plugin, "_build_common_args", return_value=[]),
            )
        if hasattr(plugin, "_build_config_args"):
            extra_patches.append(
                patch.object(plugin, "_build_config_args", return_value=[]),
            )
        if hasattr(plugin, "_create_timeout_result"):
            extra_patches.append(
                patch.object(
                    plugin,
                    "_create_timeout_result",
                    return_value=timeout_result,
                ),
            )

        from contextlib import ExitStack

        with ExitStack() as stack:
            for p in extra_patches:
                stack.enter_context(p)
            result = plugin.check(["/tmp/test_file"], {})

    assert_that(result.success).is_false()
    assert_that(result.output).is_not_none()
    assert_that(result.output.lower() if result.output else "").contains("timed out")


# =============================================================================
# Test: Fix Raises NotImplementedError
# =============================================================================


@pytest.mark.parametrize(
    ("plugin_class_path", "error_match"),
    TOOLS_THAT_CANNOT_FIX,
)
def test_fix_raises_not_implemented(
    plugin_class_path: str,
    error_match: str,
) -> None:
    """Tools that cannot fix should raise NotImplementedError.

    This test is parametrized across tools that do not support
    automatic fixing of issues.

    Args:
        plugin_class_path: Full module path to the plugin class.
        error_match: Pattern expected in the error message.
    """
    plugin = _get_plugin_instance(plugin_class_path)

    with pytest.raises(NotImplementedError, match=error_match):
        plugin.fix([], {})


# =============================================================================
# Test: Check Early Return When Should Skip
# =============================================================================


@pytest.mark.parametrize(
    ("plugin_class_path", "expected_name"),
    TOOL_EARLY_SKIP_CONFIGS,
)
def test_check_early_return_when_should_skip(
    plugin_class_path: str,
    expected_name: ToolName,
    mock_execution_context: Callable[..., MagicMock],
) -> None:
    """Check returns early result when should_skip is True.

    This test is parametrized across tools to verify that the early
    skip logic is implemented consistently.

    Args:
        plugin_class_path: Full module path to the plugin class.
        expected_name: The expected tool name.
        mock_execution_context: Factory for mock execution contexts.
    """
    plugin = _get_plugin_instance(plugin_class_path)

    early_result = MagicMock()
    early_result.success = True
    early_result.issues_count = 0

    ctx = mock_execution_context(
        should_skip=True,
        early_result=early_result,
    )

    with patch.object(plugin, "_prepare_execution", return_value=ctx):
        result = plugin.check(["/tmp"], {})

    assert_that(result.success).is_true()
