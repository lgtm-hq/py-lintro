"""Pytest error handling.

This module contains the PytestErrorHandler class that handles various error
scenarios consistently and provides standardized error messages.
"""

import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass, field

from loguru import logger

from lintro.models.core.tool_result import ToolResult


@dataclass
class PytestErrorHandler:
    """Handles pytest error scenarios consistently.

    This class encapsulates error handling logic for various pytest execution
    failures, providing standardized error messages and ToolResult objects.

    Attributes:
        tool_name: Name of the tool (e.g., "pytest").
    """

    tool_name: str = field(default="pytest")

    def handle_timeout_error(
        self,
        timeout_val: int,
        cmd: list[str],
        initial_count: int = 0,
    ) -> ToolResult:
        """Handle timeout errors consistently.

        Args:
            timeout_val: The timeout value that was exceeded.
            cmd: Command that timed out.
            initial_count: Number of issues discovered before timeout.

        Returns:
            ToolResult: Standardized timeout error result.
        """
        # Format the command for display
        cmd_str = " ".join(cmd[:4]) if len(cmd) >= 4 else " ".join(cmd)
        if len(cmd) > 4:
            cmd_str += f" ... ({len(cmd) - 4} more args)"

        error_msg = (
            f"❌ pytest execution timed out after {timeout_val}s\n\n"
            f"Command: {cmd_str}\n\n"
            "Possible causes:\n"
            "  • Tests are taking too long to run\n"
            "  • Some tests are hanging or blocked (e.g., waiting for I/O)\n"
            "  • Test discovery is slow or stuck\n"
            "  • Resource exhaustion (memory, file descriptors)\n\n"
            "Solutions:\n"
            "  1. Increase timeout: lintro test --tool-options timeout=600\n"
            "  2. Run fewer tests: lintro test tests/unit/ (vs full test suite)\n"
            "  3. Run in parallel: lintro test --tool-options workers=auto\n"
            "  4. Skip slow tests: lintro test -m 'not slow'\n"
            "  5. Debug directly: pytest -v --tb=short <test_file>\n"
        )
        logger.error(error_msg)
        return ToolResult(
            name=self.tool_name,
            success=False,
            issues=[],
            output=error_msg,
            # A timeout is an execution failure, not a finding: only genuine
            # pre-timeout issues are counted (see timeout_utils' model).
            issues_count=initial_count,
            timed_out=True,
        )

    def handle_execution_error(
        self,
        error: Exception,
        cmd: list[str],
    ) -> ToolResult:
        """Handle execution errors consistently.

        Args:
            error: The exception that occurred.
            cmd: Command that failed.

        Returns:
            ToolResult: Standardized error result.
        """
        if isinstance(error, FileNotFoundError):
            error_msg = (
                f"pytest executable not found: {error}\n\n"
                "Please ensure pytest is installed:\n"
                "  - Install via pip: pip install pytest\n"
                "  - Install via uv: uv add pytest\n"
                "  - Or install as dev dependency: uv add --dev pytest\n\n"
                "After installation, verify pytest is available:\n"
                "  pytest --version"
            )
        elif isinstance(error, subprocess.CalledProcessError):
            error_msg = (
                f"pytest execution failed with return code {error.returncode}\n\n"
                "Common causes:\n"
                "  - Syntax errors in test files\n"
                "  - Missing dependencies or imports\n"
                "  - Configuration issues in pytest.ini or pyproject.toml\n"
                "  - Permission errors accessing test files\n\n"
                "Try running pytest directly to see detailed error:\n"
                f"  {' '.join(cmd[:3])} ..."
            )
        else:
            # Generic error handling with helpful context
            error_type = type(error).__name__
            error_msg = (
                f"Unexpected error running pytest: {error_type}: {error}\n\n"
                "Please report this issue if it persists. "
                "For troubleshooting:\n"
                "  - Verify pytest is installed: pytest --version\n"
                "  - Check test files for syntax errors\n"
                "  - Review pytest configuration files\n"
                "  - Run pytest directly to see full output"
            )

        logger.error(error_msg)
        return ToolResult(
            name=self.tool_name,
            success=False,
            issues=[],
            output=error_msg,
            issues_count=0,
            timed_out=isinstance(error, subprocess.TimeoutExpired),
        )
