"""Unit tests for optional MCP SDK availability helpers."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from assertpy import assert_that
from click import UsageError

from lintro.mcp import is_mcp_available, require_mcp


def test_is_mcp_available_true_when_spec_found() -> None:
    """Availability is true when a module spec for mcp is located."""
    with patch("importlib.util.find_spec", return_value=object()):
        assert_that(is_mcp_available()).is_true()


def test_is_mcp_available_false_when_spec_missing() -> None:
    """Availability is false when no spec can be located."""
    with patch("importlib.util.find_spec", return_value=None):
        assert_that(is_mcp_available()).is_false()


@pytest.mark.parametrize("error", [ImportError("broken"), ValueError("no spec")])
def test_is_mcp_available_false_on_probe_failure(error: Exception) -> None:
    """A broken or half-installed SDK reports unavailable, never raises."""
    with patch("importlib.util.find_spec", side_effect=error):
        assert_that(is_mcp_available()).is_false()


def test_is_mcp_available_does_not_import_the_sdk() -> None:
    """The probe locates a spec rather than executing the package."""
    with patch("builtins.__import__", side_effect=AssertionError("imported mcp")):
        assert_that(is_mcp_available()).is_true()


def test_require_mcp_is_quiet_when_available() -> None:
    """require_mcp returns without raising when the SDK is present."""
    with patch("lintro.mcp.is_mcp_available", return_value=True):
        require_mcp()


def test_require_mcp_raises_usage_error_when_missing() -> None:
    """require_mcp raises a Click UsageError with install guidance."""
    with (
        patch("lintro.mcp.is_mcp_available", return_value=False),
        pytest.raises(UsageError) as exc_info,
    ):
        require_mcp()

    assert_that(str(exc_info.value)).contains("lintro[mcp]")
