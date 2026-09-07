"""Pytest configuration for semgrep tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest

from lintro.tools.semgrep.definition import SemgrepPlugin


@pytest.fixture
def semgrep_plugin() -> Generator[SemgrepPlugin, None, None]:
    """Provide a SemgrepPlugin instance for testing.

    Yields:
        SemgrepPlugin: A SemgrepPlugin instance with version checks bypassed.
    """
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        yield SemgrepPlugin()
