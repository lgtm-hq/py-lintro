"""Shared fixtures for prettier plugin tests."""

from __future__ import annotations

import pytest

from lintro.tools.prettier.definition import PrettierPlugin


@pytest.fixture
def prettier_plugin() -> PrettierPlugin:
    """Provide a PrettierPlugin instance for testing.

    Returns:
        A PrettierPlugin instance.
    """
    return PrettierPlugin()
