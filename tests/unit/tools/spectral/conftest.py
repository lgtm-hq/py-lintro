"""Shared fixtures for spectral plugin tests."""

from __future__ import annotations

import pytest

from lintro.tools.definitions.spectral import SpectralPlugin


@pytest.fixture
def spectral_plugin() -> SpectralPlugin:
    """Provide a SpectralPlugin instance for testing.

    Returns:
        SpectralPlugin: A new SpectralPlugin instance.
    """
    return SpectralPlugin()
