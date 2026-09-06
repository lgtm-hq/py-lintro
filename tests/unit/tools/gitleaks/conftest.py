"""Pytest configuration for gitleaks tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest

from lintro.tools.gitleaks.definition import GitleaksPlugin


@pytest.fixture
def gitleaks_plugin() -> Generator[GitleaksPlugin, None, None]:
    """Provide a GitleaksPlugin instance for testing.

    Yields:
        GitleaksPlugin: A GitleaksPlugin instance with version check mocked.
    """
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        yield GitleaksPlugin()
