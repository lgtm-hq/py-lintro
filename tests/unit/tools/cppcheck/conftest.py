"""Pytest configuration for cppcheck tests."""

from __future__ import annotations

import pytest

from lintro.tools.definitions.cppcheck import CppcheckPlugin


@pytest.fixture
def cppcheck_plugin() -> CppcheckPlugin:
    """Provide a CppcheckPlugin instance for testing.

    Returns:
        A CppcheckPlugin instance.
    """
    return CppcheckPlugin()
