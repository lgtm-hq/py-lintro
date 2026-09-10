"""Shared fixtures for pydoclint plugin tests."""

from __future__ import annotations

import pytest

from lintro.tools.pydoclint.definition import PydoclintPlugin


@pytest.fixture
def pydoclint_plugin() -> PydoclintPlugin:
    """Provide a PydoclintPlugin instance for testing."""
    return PydoclintPlugin()
