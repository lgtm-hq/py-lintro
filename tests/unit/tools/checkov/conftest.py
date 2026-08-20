"""Pytest configuration for checkov tests."""

from __future__ import annotations

import pytest

from lintro.tools.definitions.checkov import CheckovPlugin


@pytest.fixture
def checkov_plugin() -> CheckovPlugin:
    """Provide a CheckovPlugin instance for testing.

    Returns:
        A CheckovPlugin instance.
    """
    return CheckovPlugin()
