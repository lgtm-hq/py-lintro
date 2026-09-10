"""Pytest configuration for shfmt tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from lintro.tools.shfmt.definition import ShfmtPlugin


@pytest.fixture
def shfmt_plugin() -> ShfmtPlugin:
    """Provide a ShfmtPlugin instance for testing.

    Returns:
        A ShfmtPlugin instance.
    """
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        return ShfmtPlugin()
