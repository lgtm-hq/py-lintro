"""Pytest configuration for commitlint plugin tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest

from lintro.tools.definitions.commitlint import CommitlintPlugin


@pytest.fixture
def commitlint_plugin() -> Generator[CommitlintPlugin, None, None]:
    """Provide a CommitlintPlugin instance with the version check mocked.

    Yields:
        CommitlintPlugin: A plugin instance whose version verification is
            bypassed so tests do not require the real binary.
    """
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        yield CommitlintPlugin()
