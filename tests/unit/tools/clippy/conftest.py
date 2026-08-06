"""Pytest configuration for clippy tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest

from lintro.tools.definitions.clippy import ClippyPlugin


@pytest.fixture
def clippy_plugin() -> Generator[ClippyPlugin, None, None]:
    """Provide a ClippyPlugin instance for testing.

    Yields:
        ClippyPlugin: A ClippyPlugin instance with version checks mocked.
    """
    with (
        patch.object(
            ClippyPlugin,
            "_verify_tool_version",
            return_value=None,
        ),
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
    ):
        yield ClippyPlugin()
