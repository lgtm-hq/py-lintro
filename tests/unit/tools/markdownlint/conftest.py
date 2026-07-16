"""Pytest configuration for markdownlint tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest

from lintro.tools.definitions.markdownlint import MarkdownlintPlugin


@pytest.fixture
def markdownlint_plugin() -> Generator[MarkdownlintPlugin, None, None]:
    """Provide a MarkdownlintPlugin instance for testing.

    Yields:
        MarkdownlintPlugin: A MarkdownlintPlugin instance with version check mocked.
    """
    with patch.object(
        MarkdownlintPlugin,
        "_verify_tool_version",
        return_value=None,
    ):
        yield MarkdownlintPlugin()
