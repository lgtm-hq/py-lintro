"""Pytest configuration for djLint plugin tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest

from lintro.tools.definitions.djlint import DjlintPlugin


@pytest.fixture
def djlint_plugin() -> Generator[DjlintPlugin, None, None]:
    """Provide a DjlintPlugin instance for testing.

    The version check is patched for the whole fixture lifetime so that
    check()/fix() calls do not fail on version verification.

    Yields:
        DjlintPlugin: A DjlintPlugin instance with version checking disabled.
    """
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        yield DjlintPlugin()
