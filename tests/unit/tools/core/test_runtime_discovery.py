"""Tests for lintro.tools.core.runtime_discovery module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.tools.core.runtime_discovery import (
    DiscoveredTool,
    _extract_version,
    clear_discovery_cache,
    discover_all_tools,
    discover_tool,
    format_tool_status_table,
    get_available_tools,
    get_tool_path,
    get_unavailable_tools,
    is_tool_available,
)


@pytest.fixture(autouse=True)
def _clear_cache_before_each_test() -> None:
    """Clear discovery cache before each test."""
    clear_discovery_cache()


def test_extract_version_semantic() -> None:
    """Extract semantic version from output."""
    assert_that(_extract_version("ruff 0.1.0")).is_equal_to("0.1.0")


def test_extract_version_with_prefix() -> None:
    """Extract version with v prefix."""
    assert_that(_extract_version("tool v1.2.3")).is_equal_to("1.2.3")


def test_extract_version_keyword() -> None:
    """Extract version after 'version' keyword."""
    assert_that(_extract_version("black, version 23.0.0")).is_equal_to("23.0.0")


def test_extract_version_multiline() -> None:
    """Extract version from multiline output."""
    output = "mypy 1.0.0 (compiled: yes)\nPython 3.11"
    assert_that(_extract_version(output)).is_equal_to("1.0.0")


def test_extract_version_returns_none_for_no_version() -> None:
    """Return None when no version found."""
    assert_that(_extract_version("no version here")).is_none()


def test_discover_tool_available() -> None:
    """Discover tool that exists in PATH."""
    with (
        patch("shutil.which", return_value="/usr/bin/ruff"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="ruff 0.1.0",
            stderr="",
        )
        result = discover_tool("ruff", use_cache=False)

        assert_that(result.name).is_equal_to("ruff")
        assert_that(result.path).is_equal_to("/usr/bin/ruff")
        assert_that(result.version).is_equal_to("0.1.0")
        assert_that(result.available).is_true()


def test_discover_tool_unavailable() -> None:
    """Handle tool not found in PATH."""
    with patch("shutil.which", return_value=None):
        result = discover_tool("nonexistent", use_cache=False)

        assert_that(result.name).is_equal_to("nonexistent")
        assert_that(result.available).is_false()
        assert_that(result.error_message).contains("not found in PATH")


def test_discover_tool_handles_timeout() -> None:
    """Tool is unavailable when version probe times out."""
    import subprocess

    with (
        patch("shutil.which", return_value="/usr/bin/slow_tool"),
        patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["tool"], timeout=5),
        ),
    ):
        result = discover_tool("slow_tool", use_cache=False)

        assert_that(result.available).is_false()
        assert_that(result.version).is_none()


def test_discover_tool_uses_cache() -> None:
    """Use cache by default on second call."""
    with (
        patch("shutil.which", return_value="/usr/bin/ruff") as mock_which,
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="ruff 0.1.0", stderr="")

        discover_tool("ruff")
        discover_tool("ruff")

        assert_that(mock_which.call_count).is_equal_to(1)


def test_discover_all_tools() -> None:
    """Discover all tools in TOOL_VERSION_COMMANDS."""
    with (
        patch("shutil.which", return_value="/usr/bin/tool"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="1.0.0", stderr="")

        tools = discover_all_tools(use_cache=False)

        assert_that(len(tools)).is_greater_than(5)
        assert_that(tools).contains_key("ruff", "black", "mypy")


def test_is_tool_available_returns_true() -> None:
    """is_tool_available returns True for available tool."""
    with (
        patch("shutil.which", return_value="/usr/bin/ruff"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="ruff 0.1.0", stderr="")
        assert_that(is_tool_available("ruff")).is_true()


def test_is_tool_available_returns_false() -> None:
    """is_tool_available returns False for unavailable tool."""
    with patch("shutil.which", return_value=None):
        assert_that(is_tool_available("nonexistent")).is_false()


def test_get_tool_path_returns_path() -> None:
    """get_tool_path returns path for available tool."""
    with (
        patch("shutil.which", return_value="/usr/bin/ruff"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="ruff 0.1.0", stderr="")
        assert_that(get_tool_path("ruff")).is_equal_to("/usr/bin/ruff")


def test_get_tool_path_returns_none() -> None:
    """get_tool_path returns None for unavailable tool."""
    with patch("shutil.which", return_value=None):
        assert_that(get_tool_path("nonexistent")).is_none()


def test_get_unavailable_tools() -> None:
    """get_unavailable_tools returns list of missing tools."""

    def mock_which(name: str) -> str | None:
        return "/usr/bin/ruff" if name == "ruff" else None

    with (
        patch("shutil.which", side_effect=mock_which),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="1.0.0", stderr="")
        unavailable = get_unavailable_tools()
        assert_that(unavailable).does_not_contain("ruff")
        assert_that(len(unavailable)).is_greater_than(0)


def test_get_available_tools() -> None:
    """get_available_tools returns list of available tools."""

    def mock_which(name: str) -> str | None:
        return "/usr/bin/ruff" if name == "ruff" else None

    with (
        patch("shutil.which", side_effect=mock_which),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="1.0.0", stderr="")
        available = get_available_tools()
        assert_that(available).contains("ruff")


def test_format_tool_status_table() -> None:
    """Format status table with discovered tools."""
    with (
        patch("shutil.which", return_value="/usr/bin/tool"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="1.0.0", stderr="")
        table = format_tool_status_table()
        assert_that(table).contains("Tool Discovery Status")


def test_clear_discovery_cache() -> None:
    """Clear cache makes next call rediscover tools."""
    with (
        patch("shutil.which", return_value="/usr/bin/ruff") as mock_which,
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="ruff 0.1.0", stderr="")

        discover_tool("ruff")
        initial_count = mock_which.call_count

        clear_discovery_cache()
        discover_tool("ruff")

        assert_that(mock_which.call_count).is_equal_to(initial_count + 1)


def test_discovered_tool_default_values() -> None:
    """DiscoveredTool has correct defaults."""
    tool = DiscoveredTool(name="test")

    assert_that(tool.name).is_equal_to("test")
    assert_that(tool.path).is_equal_to("")
    assert_that(tool.version).is_none()
    assert_that(tool.available).is_false()
