"""Unit tests for config_reporting module."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.utils.config_reporting import get_config_report, print_config_report


@pytest.fixture
def mock_tool_config_summary() -> dict[str, Any]:
    """Create mock tool config summary.

    Returns:
        Dictionary containing mock tool config summary.
    """
    mock_info = MagicMock()
    mock_info.is_injectable = True
    mock_info.effective_config = {"line_length": 88}
    mock_info.lintro_tool_config = {"line_length": 88}
    mock_info.native_config = None
    return {"ruff": mock_info}


@pytest.fixture
def standard_patches(mock_tool_config_summary: dict[str, Any]) -> tuple[Any, ...]:
    """Provide standard patches for get_config_report tests.

    Args:
        mock_tool_config_summary: Mock tool config summary fixture.

    Returns:
        Tuple of patch objects for testing.
    """
    return (
        patch(
            "lintro.utils.unified_config.get_tool_config_summary",
            return_value=mock_tool_config_summary,
        ),
        patch(
            "lintro.utils.config_reporting.get_effective_line_length",
            return_value=88,
        ),
        patch(
            "lintro.utils.config_reporting.get_tool_order_config",
            return_value={"strategy": "priority"},
        ),
        patch(
            "lintro.utils.config_reporting.get_ordered_tools",
            return_value=["ruff"],
        ),
        patch(
            "lintro.utils.config_reporting.get_tool_priority",
            return_value=100,
        ),
        patch(
            "lintro.utils.config_reporting.validate_config_consistency",
            return_value=[],
        ),
    )


# --- get_config_report tests ---


def test_report_contains_header(standard_patches: tuple[Any, ...]) -> None:
    """Test report contains header section.

    Args:
        standard_patches: Standard patches for testing.
    """
    with (
        standard_patches[0],
        standard_patches[1],
        standard_patches[2],
        standard_patches[3],
        standard_patches[4],
        standard_patches[5],
    ):
        report = get_config_report()

        assert_that(report).contains("LINTRO CONFIGURATION REPORT")
        assert_that(report).contains("=" * 60)


def test_report_contains_global_settings(standard_patches: tuple[Any, ...]) -> None:
    """Test report contains global settings section.

    Args:
        standard_patches: Standard patches for testing.
    """
    with (
        standard_patches[0],
        standard_patches[1],
        standard_patches[2],
        standard_patches[3],
        standard_patches[4],
        standard_patches[5],
    ):
        report = get_config_report()

        assert_that(report).contains("── Global Settings ──")
        assert_that(report).contains("Central line_length: 88")
        assert_that(report).contains("Tool order strategy: priority")


def test_report_contains_tool_execution_order(
    standard_patches: tuple[Any, ...],
) -> None:
    """Test report contains tool execution order section.

    Args:
        standard_patches: Standard patches for testing.
    """
    with (
        standard_patches[0],
        standard_patches[1],
        standard_patches[2],
        standard_patches[3],
        standard_patches[4],
        standard_patches[5],
    ):
        report = get_config_report()

        assert_that(report).contains("── Tool Execution Order ──")
        assert_that(report).contains("1. ruff (priority: 100)")


def test_report_contains_per_tool_config(standard_patches: tuple[Any, ...]) -> None:
    """Test report contains per-tool configuration section.

    Args:
        standard_patches: Standard patches for testing.
    """
    with (
        standard_patches[0],
        standard_patches[1],
        standard_patches[2],
        standard_patches[3],
        standard_patches[4],
        standard_patches[5],
    ):
        report = get_config_report()

        assert_that(report).contains("── Per-Tool Configuration ──")
        assert_that(report).contains("ruff:")
        assert_that(report).contains("Status: ✅ Syncable")
        assert_that(report).contains("Effective line_length: 88")


def test_report_shows_native_only_for_non_injectable() -> None:
    """Test non-injectable tools show native only status."""
    mock_info = MagicMock()
    mock_info.is_injectable = False
    mock_info.effective_config = {"line_length": 80}
    mock_info.lintro_tool_config = None
    mock_info.native_config = {"some": "config"}

    with (
        patch(
            "lintro.utils.unified_config.get_tool_config_summary",
            return_value={"prettier": mock_info},
        ),
        patch(
            "lintro.utils.config_reporting.get_effective_line_length",
            return_value=88,
        ),
        patch(
            "lintro.utils.config_reporting.get_tool_order_config",
            return_value={"strategy": "priority"},
        ),
        patch(
            "lintro.utils.config_reporting.get_ordered_tools",
            return_value=["prettier"],
        ),
        patch(
            "lintro.utils.config_reporting.get_tool_priority",
            return_value=50,
        ),
        patch(
            "lintro.utils.config_reporting.validate_config_consistency",
            return_value=[],
        ),
    ):
        report = get_config_report()
        assert_that(report).contains("Status: ⚠️ Native only")


def test_report_shows_warnings(standard_patches: tuple[Any, ...]) -> None:
    """Test report shows warnings when present.

    Args:
        standard_patches: Standard patches for testing.
    """
    warnings = ["Warning 1: Config mismatch", "Warning 2: Missing config"]

    with (
        standard_patches[0],
        standard_patches[1],
        standard_patches[2],
        standard_patches[3],
        standard_patches[4],
        patch(
            "lintro.utils.config_reporting.validate_config_consistency",
            return_value=warnings,
        ),
    ):
        report = get_config_report()

        assert_that(report).contains("── Configuration Warnings ──")
        assert_that(report).contains("Warning 1: Config mismatch")
        assert_that(report).contains("Warning 2: Missing config")


def test_report_shows_no_warnings_message(standard_patches: tuple[Any, ...]) -> None:
    """Test report shows no warnings message when consistent.

    Args:
        standard_patches: Standard patches for testing.
    """
    with (
        standard_patches[0],
        standard_patches[1],
        standard_patches[2],
        standard_patches[3],
        standard_patches[4],
        standard_patches[5],
    ):
        report = get_config_report()
        assert_that(report).contains("None - all configs consistent!")


def test_report_with_custom_order(mock_tool_config_summary: dict[str, Any]) -> None:
    """Test report shows custom order when configured.

    Args:
        mock_tool_config_summary: Mock tool config summary.
    """
    with (
        patch(
            "lintro.utils.unified_config.get_tool_config_summary",
            return_value=mock_tool_config_summary,
        ),
        patch(
            "lintro.utils.config_reporting.get_effective_line_length",
            return_value=88,
        ),
        patch(
            "lintro.utils.config_reporting.get_tool_order_config",
            return_value={"strategy": "custom", "custom_order": ["ruff", "mypy"]},
        ),
        patch(
            "lintro.utils.config_reporting.get_ordered_tools",
            return_value=["ruff"],
        ),
        patch(
            "lintro.utils.config_reporting.get_tool_priority",
            return_value=100,
        ),
        patch(
            "lintro.utils.config_reporting.validate_config_consistency",
            return_value=[],
        ),
    ):
        report = get_config_report()
        assert_that(report).contains("Custom order: ruff, mypy")


def test_report_line_length_not_configured(
    mock_tool_config_summary: dict[str, Any],
) -> None:
    """Test report shows Not configured when line_length is None.

    Args:
        mock_tool_config_summary: Mock tool config summary.
    """
    with (
        patch(
            "lintro.utils.unified_config.get_tool_config_summary",
            return_value=mock_tool_config_summary,
        ),
        patch(
            "lintro.utils.config_reporting.get_effective_line_length",
            return_value=None,
        ),
        patch(
            "lintro.utils.config_reporting.get_tool_order_config",
            return_value={"strategy": "priority"},
        ),
        patch(
            "lintro.utils.config_reporting.get_ordered_tools",
            return_value=["ruff"],
        ),
        patch(
            "lintro.utils.config_reporting.get_tool_priority",
            return_value=100,
        ),
        patch(
            "lintro.utils.config_reporting.validate_config_consistency",
            return_value=[],
        ),
    ):
        report = get_config_report()
        assert_that(report).contains("Central line_length: Not configured")


# --- print_config_report tests ---


def _print_report_records(report: str) -> list[tuple[str, str]]:
    """Print a canned report and return the records loguru actually received.

    Args:
        report: Report body that ``get_config_report`` should return.

    Returns:
        list[tuple[str, str]]: One ``(level name, message)`` pair per record
        emitted while printing the report.
    """
    records: list[tuple[str, str]] = []
    handler_id = logger.add(
        lambda message: records.append(
            (message.record["level"].name, message.record["message"]),
        ),
        level="DEBUG",
    )
    try:
        with patch(
            "lintro.utils.config_reporting.get_config_report",
            return_value=report,
        ):
            print_config_report()
    finally:
        logger.remove(handler_id)
    return records


def test_print_logs_report_lines() -> None:
    """Every line of the report reaches the logger at INFO level."""
    records = _print_report_records(
        report="── Global Settings ──\n  line_length: 88\n── End ──",
    )

    assert_that(records).is_equal_to(
        [
            ("INFO", "── Global Settings ──"),
            ("INFO", "  line_length: 88"),
            ("INFO", "── End ──"),
        ],
    )


def test_print_warnings_logged_at_warning_level() -> None:
    """Indented lines inside the warnings section are logged as warnings."""
    records = _print_report_records(
        report="── Configuration Warnings ──\n  Warning: Config mismatch\n── End ──",
    )

    assert_that(records).contains(("WARNING", "  Warning: Config mismatch"))
    assert_that(records).contains(("INFO", "── Configuration Warnings ──"))


def test_print_non_warning_lines_logged_at_info() -> None:
    """Indented lines outside the warnings section stay at INFO level."""
    records = _print_report_records(
        report="── Global Settings ──\n  line_length: 88",
    )

    assert_that(records).contains(("INFO", "  line_length: 88"))
    assert_that([level for level, _ in records]).does_not_contain("WARNING")
