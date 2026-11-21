"""Pytest configuration for black tests."""

from __future__ import annotations

import shutil
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from lintro.tools.black.definition import BlackPlugin


@pytest.fixture(autouse=True)
def _ruff_from_this_environment() -> Generator[None, None, None]:
    """Resolve ``ruff`` from this test environment, whatever ``PATH`` holds.

    Black's E501 supplement runs ruff, and a skipped supplement means Black's
    result proves no complete inspection (#3578). Without this, the outcome
    of every check test would depend on whether the machine has ruff on
    ``PATH``. Tests that simulate a missing ruff patch ``shutil.which``
    themselves, and their patch takes precedence.

    Yields:
        None: While ``shutil.which("ruff")`` names this environment's ruff.
    """
    real_which = shutil.which
    own_ruff = real_which("ruff", path=str(Path(sys.executable).parent))

    def which(name: str, *args: Any, **kwargs: Any) -> str | None:
        if name == "ruff" and own_ruff is not None:
            return own_ruff
        return real_which(name, *args, **kwargs)

    with patch("shutil.which", side_effect=which):
        yield


@pytest.fixture
def black_plugin() -> Generator[BlackPlugin, None, None]:
    """Provide a BlackPlugin instance for testing.

    Yields:
        BlackPlugin: A BlackPlugin instance with version check mocked.
    """
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        yield BlackPlugin()
