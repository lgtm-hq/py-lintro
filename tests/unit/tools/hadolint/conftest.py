"""Pytest configuration for hadolint tests."""

from __future__ import annotations

import pytest

from lintro.tools.hadolint.definition import HadolintPlugin


@pytest.fixture
def hadolint_plugin() -> HadolintPlugin:
    """Provide a HadolintPlugin instance for testing.

    Returns:
        A HadolintPlugin instance.
    """
    return HadolintPlugin()
