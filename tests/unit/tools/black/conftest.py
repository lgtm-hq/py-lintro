"""Pytest configuration for black tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest

from lintro.tools.definitions.black import BlackPlugin


@pytest.fixture
def black_plugin() -> Generator[BlackPlugin, None, None]:
    """Provide a BlackPlugin instance for testing.

    Yields:
        BlackPlugin: A BlackPlugin instance with version check mocked.
    """
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        yield BlackPlugin()
