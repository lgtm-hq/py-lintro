"""Pytest configuration for dotenv-linter plugin tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from lintro.tools.definitions.dotenv_linter import DotenvLinterPlugin


@pytest.fixture
def dotenv_linter_plugin() -> DotenvLinterPlugin:
    """Provide a DotenvLinterPlugin with the version check mocked out.

    Returns:
        A DotenvLinterPlugin instance whose version verification is bypassed.
    """
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        return DotenvLinterPlugin()
