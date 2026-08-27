"""Pytest configuration for vue-tsc tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest

from lintro.tools.definitions.vue_tsc import VueTscPlugin


@pytest.fixture
def vue_tsc_plugin(
    no_local_node_install: None,
) -> Generator[VueTscPlugin, None, None]:
    """Provide a VueTscPlugin instance for testing.

    Args:
        no_local_node_install: Ensure command resolution is independent of the
            checkout's installed Node dependencies before constructing the
            plugin and caching its version command.

    Yields:
        VueTscPlugin: A VueTscPlugin instance with version check mocked.
    """
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        yield VueTscPlugin()
