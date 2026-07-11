"""Pytest configuration for yamllint tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest

from lintro.tools.definitions.yamllint import YamllintPlugin


@pytest.fixture
def yamllint_plugin() -> Generator[YamllintPlugin, None, None]:
    """Provide a YamllintPlugin instance for testing.

    Yields:
        YamllintPlugin: A YamllintPlugin instance with version check mocked.
    """
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        yield YamllintPlugin()
