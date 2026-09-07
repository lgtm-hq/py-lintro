"""Shared fixtures for unified_config tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest


@pytest.fixture
def mock_empty_configs() -> Generator[None]:
    """Mock all config loaders to return empty dicts.

    Yields:
        None: Context manager for mocking all config loaders.
    """
    with (
        patch(
            "lintro.utils.config_priority.load_lintro_tool_config",
            return_value={},
        ),
        patch(
            "lintro.utils.config_priority.load_lintro_global_config",
            return_value={},
        ),
        patch("lintro.utils.config_priority.load_pyproject", return_value={}),
        patch(
            "lintro.utils.config_priority._load_native_tool_config",
            return_value={},
        ),
    ):
        yield
