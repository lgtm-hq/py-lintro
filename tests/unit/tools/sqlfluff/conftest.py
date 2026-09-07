"""Pytest configuration for sqlfluff tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest

from lintro.tools.sqlfluff.definition import SqlfluffPlugin


@pytest.fixture
def sqlfluff_plugin() -> Generator[SqlfluffPlugin, None, None]:
    """Provide a SqlfluffPlugin instance for testing.

    The verify_tool_version is patched for the entire lifetime of the fixture
    to prevent version check failures during check()/fix() calls.

    Yields:
        SqlfluffPlugin: A SqlfluffPlugin instance with version checking disabled.
    """
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        yield SqlfluffPlugin()
