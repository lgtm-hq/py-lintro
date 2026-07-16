"""Pytest execution logic.

This module contains the PytestExecutor class that handles test execution
and subprocess operations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from lintro.tools.implementations.pytest.addopts_coverage import (
    config_addopts_enable_coverage,
)
from lintro.tools.implementations.pytest.collection import get_cpu_count
from lintro.tools.implementations.pytest.markers import collect_tests_once
from lintro.tools.implementations.pytest.pytest_config import PytestConfiguration

if TYPE_CHECKING:
    from lintro.tools.definitions.pytest import PytestPlugin


@dataclass
class PytestExecutor:
    """Handles pytest test execution.

    This class encapsulates the logic for executing pytest tests
    and handling subprocess operations.

    Attributes:
        config: PytestConfiguration instance with test execution options.
        tool: Reference to the parent tool for subprocess execution.
    """

    config: PytestConfiguration
    tool: PytestPlugin | None  # Required: must be set by the parent tool

    def prepare_test_execution(
        self,
        target_files: list[str],
    ) -> int:
        """Prepare test execution by collecting tests.

        Args:
            target_files: Files or directories to test.

        Raises:
            ValueError: If tool reference is not set.

        Returns:
            int: Total number of available tests.
        """
        if self.tool is None:
            raise ValueError("Tool reference not set on executor")

        # Collect tests to get total count
        total_available_tests = collect_tests_once(
            self.tool,
            target_files,
        )

        return total_available_tests

    def execute_tests(
        self,
        cmd: list[str],
    ) -> tuple[bool, str, int]:
        """Execute pytest tests and parse output.

        Args:
            cmd: Command to execute.

        Raises:
            ValueError: If tool reference is not set.

        Returns:
            Tuple[bool, str, int]: Tuple of (success, output, return_code).
        """
        if self.tool is None:
            raise ValueError("Tool reference not set on executor")

        success, output = self.tool._run_subprocess(cmd)
        # Parse output with actual success status
        # (pytest returns non-zero on failures)
        return_code = 0 if success else 1
        return (success, output, return_code)

    def display_run_config(
        self,
        total_tests: int,
        target_files: list[str],
    ) -> None:
        """Display test run configuration summary.

        Args:
            total_tests: Total number of tests discovered.
            target_files: List of target files/directories.
        """
        import click

        options = self.config.get_options_dict()

        # Get worker configuration
        workers = options.get("workers")
        parallel_preset = options.get("parallel_preset")
        if parallel_preset:
            worker_display = f"{parallel_preset} preset"
        elif workers == "auto" or workers is None:
            cpu_count = get_cpu_count()
            worker_display = f"auto ({cpu_count} CPUs)"
        elif workers and str(workers) != "0":
            worker_display = str(workers)
        else:
            worker_display = "disabled"

        # Get coverage configuration. Coverage may be enabled either by lintro's
        # own options or by ``--cov`` flags declared in the project's pytest
        # configuration ``addopts`` (which pytest injects independently). Detect
        # both so the banner never contradicts the coverage that actually runs.
        coverage_enabled = any(
            [
                options.get("coverage_term_missing"),
                options.get("coverage_html"),
                options.get("coverage_xml"),
                options.get("coverage_report"),
                options.get("coverage_threshold") is not None,
                config_addopts_enable_coverage(),
            ],
        )
        coverage_display = "enabled" if coverage_enabled else "disabled"

        # Format paths display
        if len(target_files) == 1:
            paths_display = target_files[0]
        elif len(target_files) <= 3:
            paths_display = ", ".join(target_files)
        else:
            paths_display = f"{target_files[0]} (+{len(target_files) - 1} more)"

        # Build and display config summary
        config_line = (
            f"Tests: {total_tests} | "
            f"Parallel: {worker_display} | "
            f"Coverage: {coverage_display} | "
            f"Path: {paths_display}"
        )
        click.echo(click.style(f"[LINTRO] {config_line}", fg="cyan"))
