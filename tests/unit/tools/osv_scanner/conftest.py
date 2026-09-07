"""Pytest configuration for OSV-Scanner tests."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest

from lintro.tools.osv_scanner.definition import OsvScannerPlugin


@pytest.fixture
def osv_scanner_plugin() -> Iterator[OsvScannerPlugin]:
    """Provide an OsvScannerPlugin instance for testing.

    Yields:
        OsvScannerPlugin: An OsvScannerPlugin instance with version checks bypassed.
    """
    with patch(
        "lintro.tools.osv_scanner.definition.verify_tool_version",
        return_value=None,
    ):
        yield OsvScannerPlugin()
