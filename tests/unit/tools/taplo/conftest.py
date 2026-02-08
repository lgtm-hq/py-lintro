"""Pytest configuration for taplo tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest

from lintro.tools.definitions.taplo import TaploPlugin


@pytest.fixture
def taplo_plugin() -> Generator[TaploPlugin, None, None]:
    """Provide a TaploPlugin instance for testing.

    Yields:
        TaploPlugin: A TaploPlugin instance with version checks bypassed.
    """
    with (
        patch(
            "lintro.plugins.base.verify_tool_version",
            return_value=None,
        ),
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
    ):
        yield TaploPlugin()
