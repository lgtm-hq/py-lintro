"""Shared fixtures for oxlint plugin tests."""

from __future__ import annotations

import pytest

from lintro.tools.oxlint.definition import OxlintPlugin


@pytest.fixture
def oxlint_plugin() -> OxlintPlugin:
    """Provide an OxlintPlugin instance for testing.

    Returns:
        OxlintPlugin: A new OxlintPlugin instance.
    """
    return OxlintPlugin()
