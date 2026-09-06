"""Pytest configuration for rustfmt tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from lintro.tools.rustfmt.definition import RustfmtPlugin


@pytest.fixture
def rustfmt_plugin() -> RustfmtPlugin:
    """Provide a RustfmtPlugin instance for testing.

    Returns:
        A RustfmtPlugin instance.
    """
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        return RustfmtPlugin()
