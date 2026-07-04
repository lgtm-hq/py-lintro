"""Unit tests for write_output_file function - Common behaviors.

Tests verify common behavior across all output formats.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from assertpy import assert_that

from lintro.enums.action import Action
from lintro.enums.output_format import OutputFormat
from lintro.utils.output.file_writer import write_output_file

if TYPE_CHECKING:
    from collections.abc import Callable

    from lintro.models.core.tool_result import ToolResult


def test_write_output_file_creates_parent_directories(
    tmp_path: Path,
    mock_tool_result_factory: Callable[..., ToolResult],
) -> None:
    """Verify parent directories are created when they don't exist.

    Args:
        tmp_path: Temporary directory path for test output.
        mock_tool_result_factory: Factory for creating mock tool results.
    """
    output_path = tmp_path / "nested" / "deeply" / "path" / "report.txt"
    results = [mock_tool_result_factory(name="ruff")]

    write_output_file(
        output_path=str(output_path),
        output_format=OutputFormat.PLAIN,
        all_results=results,
        action=Action.CHECK,
        total_issues=0,
        total_fixed=0,
    )

    assert_that(output_path.exists()).is_true()
    assert_that(output_path.parent.exists()).is_true()
    assert_that(output_path.read_text()).is_not_empty()
