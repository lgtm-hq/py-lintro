"""Pytest configuration for trivy tests."""

from __future__ import annotations

import pytest

from lintro.tools.definitions.trivy import TrivyPlugin


@pytest.fixture
def trivy_plugin() -> TrivyPlugin:
    """Provide a TrivyPlugin instance for testing.

    Returns:
        A TrivyPlugin instance.
    """
    return TrivyPlugin()
