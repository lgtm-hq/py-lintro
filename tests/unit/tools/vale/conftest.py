"""Pytest configuration for Vale plugin tests."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from lintro.tools.definitions.vale import ValePlugin


@pytest.fixture
def vale_plugin() -> ValePlugin:
    """Provide a ValePlugin instance for testing.

    Returns:
        A ValePlugin instance.
    """
    return ValePlugin()


def make_ctx(tmp_path: str, files: list[str]) -> MagicMock:
    """Build a mock execution context for check() tests.

    Args:
        tmp_path: Working directory for the context.
        files: List of discovered file paths.

    Returns:
        A MagicMock shaped like an ExecutionContext (not skipping).
    """
    ctx = MagicMock()
    ctx.should_skip = False
    ctx.early_result = None
    ctx.timeout = 30
    ctx.cwd = tmp_path
    ctx.files = files
    ctx.rel_files = files
    return ctx


def vale_output(alerts_by_file: dict[str, list[dict[str, object]]]) -> str:
    """Serialize a mapping of file to alerts as Vale JSON output.

    Args:
        alerts_by_file: Mapping of file path to a list of alert dicts.

    Returns:
        A JSON string in Vale's ``--output=JSON`` shape.
    """
    return json.dumps(alerts_by_file)
