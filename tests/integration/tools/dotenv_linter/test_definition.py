"""Integration tests validating the dotenv-linter plugin registration."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

pytestmark = pytest.mark.skipif(
    shutil.which("dotenv-linter") is None,
    reason="dotenv-linter not installed",
)


def test_plugin_is_registered(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """The dotenv-linter plugin resolves from the registry.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    plugin = get_plugin("dotenv_linter")
    assert_that(plugin).is_not_none()
    assert_that(plugin.definition.name).is_equal_to("dotenv_linter")
    assert_that(plugin.definition.can_fix).is_true()


def test_version_command_resolves_binary(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """The plugin's executable command maps to the real binary name.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    plugin = get_plugin("dotenv_linter")
    command = plugin._get_executable_command("dotenv_linter")
    assert_that(command).is_equal_to(["dotenv-linter"])
