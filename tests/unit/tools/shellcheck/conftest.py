"""Pytest configuration for shellcheck tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest

from lintro.tools.shellcheck.definition import ShellcheckPlugin


@pytest.fixture
def shellcheck_plugin() -> Generator[ShellcheckPlugin, None, None]:
    """Provide a ShellcheckPlugin instance for testing.

    Yields:
        ShellcheckPlugin: A ShellcheckPlugin instance with version check mocked.
    """
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        yield ShellcheckPlugin()
