"""Pytest configuration for typos plugin tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest

from lintro.tools.definitions.typos import TyposPlugin


@pytest.fixture
def typos_plugin() -> Generator[TyposPlugin, None, None]:
    """Provide a TyposPlugin instance with the version check mocked.

    Yields:
        TyposPlugin: A plugin instance whose version verification is a no-op.
    """
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        yield TyposPlugin()
