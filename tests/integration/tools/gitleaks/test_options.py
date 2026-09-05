"""Integration tests for GitleaksPlugin.set_options method."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

from tests.integration._tools import require_tool

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

pytestmark = require_tool("gitleaks", version_args=("version",))


@pytest.mark.parametrize(
    ("option_name", "option_value"),
    [
        ("no_git", True),
        ("no_git", False),
        ("redact", True),
        ("redact", False),
        ("config", "/path/to/config.toml"),
        ("baseline_path", "/path/to/baseline.json"),
        ("max_target_megabytes", 50),
    ],
    ids=[
        "no_git_true",
        "no_git_false",
        "redact_true",
        "redact_false",
        "config_path",
        "baseline_path",
        "max_target_megabytes",
    ],
)
def test_set_options(
    get_plugin: Callable[[str], BaseToolPlugin],
    option_name: str,
    option_value: object,
) -> None:
    """Verify GitleaksPlugin.set_options correctly sets various options.

    Tests that plugin options can be set and retrieved correctly.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        option_name: Name of the option to set.
        option_value: Value to set for the option.
    """
    gitleaks_plugin = get_plugin("gitleaks")
    gitleaks_plugin.set_options(**{option_name: option_value})
    assert_that(gitleaks_plugin.options.get(option_name)).is_equal_to(option_value)
