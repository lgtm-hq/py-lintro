"""Pytest configuration for oxfmt tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest

from lintro.tools.oxfmt.definition import OxfmtPlugin


@pytest.fixture
def oxfmt_plugin() -> Generator[OxfmtPlugin, None, None]:
    """Provide an OxfmtPlugin instance for testing.

    Yields:
        OxfmtPlugin: A plugin instance with mocked version verification.
    """
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        yield OxfmtPlugin()
