"""Pytest configuration for actionlint tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest

from lintro.tools.definitions.actionlint import ActionlintPlugin


@pytest.fixture
def actionlint_plugin() -> Generator[ActionlintPlugin, None, None]:
    """Provide an ActionlintPlugin instance for testing.

    Yields:
        ActionlintPlugin: An ActionlintPlugin instance with version check mocked.
    """
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        yield ActionlintPlugin()
