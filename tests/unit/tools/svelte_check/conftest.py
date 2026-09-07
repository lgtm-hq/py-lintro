"""Pytest configuration for svelte-check tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest

from lintro.tools.svelte_check.definition import SvelteCheckPlugin


@pytest.fixture
def svelte_check_plugin() -> Generator[SvelteCheckPlugin, None, None]:
    """Provide a SvelteCheckPlugin instance for testing.

    Yields:
        SvelteCheckPlugin: A SvelteCheckPlugin instance with version check mocked.
    """
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        yield SvelteCheckPlugin()
