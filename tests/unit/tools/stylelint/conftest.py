"""Shared fixtures for stylelint plugin tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lintro.tools.definitions.stylelint import StylelintPlugin


@pytest.fixture
def stylelint_plugin() -> StylelintPlugin:
    """Provide a StylelintPlugin instance for testing.

    Returns:
        StylelintPlugin: A new StylelintPlugin instance.
    """
    return StylelintPlugin()


def make_ctx(tmp_path: object, rel_files: list[str]) -> MagicMock:
    """Build a mocked execution context that skips file discovery.

    Args:
        tmp_path: Working directory to report as ``cwd``.
        rel_files: Relative file names the tool should operate on.

    Returns:
        MagicMock: A context object mimicking ``_prepare_execution``.
    """
    ctx = MagicMock()
    ctx.should_skip = False
    ctx.early_result = None
    ctx.timeout = 30
    ctx.cwd = str(tmp_path)
    ctx.rel_files = rel_files
    ctx.files = [f"{tmp_path}/{name}" for name in rel_files]
    return ctx
