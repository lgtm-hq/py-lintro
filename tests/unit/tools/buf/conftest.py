"""Pytest configuration for buf tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest

from lintro.tools.definitions.buf import BufPlugin


@pytest.fixture
def buf_plugin() -> Generator[BufPlugin, None, None]:
    """Provide a BufPlugin instance with version checks bypassed.

    Yields:
        BufPlugin: A BufPlugin instance safe to run without buf installed.
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
        yield BufPlugin()
