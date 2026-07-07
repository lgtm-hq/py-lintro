"""Pytest configuration for html-validate tests."""

from __future__ import annotations

import pytest

from lintro.tools.definitions.html_validate import HtmlValidatePlugin


@pytest.fixture
def html_validate_plugin() -> HtmlValidatePlugin:
    """Provide an HtmlValidatePlugin instance for testing.

    Returns:
        An HtmlValidatePlugin instance.
    """
    return HtmlValidatePlugin()
