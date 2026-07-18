"""Pytest configuration for terraform tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest

from lintro.tools.definitions.terraform import TerraformPlugin


@pytest.fixture
def terraform_plugin() -> Generator[TerraformPlugin, None, None]:
    """Provide a TerraformPlugin instance for testing.

    Yields:
        TerraformPlugin: A TerraformPlugin instance with version checks bypassed.
    """
    with (
        patch(
            "lintro.plugins.base.verify_tool_version",
            return_value=None,
        ),
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
    ):
        yield TerraformPlugin()
