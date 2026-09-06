"""Pytest configuration for astro-check tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest

from lintro.tools.astro_check.definition import AstroCheckPlugin


@pytest.fixture
def astro_check_plugin() -> Generator[AstroCheckPlugin, None, None]:
    """Provide an AstroCheckPlugin instance for testing.

    Yields:
        AstroCheckPlugin: An AstroCheckPlugin instance with version check mocked.
    """
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        yield AstroCheckPlugin()
