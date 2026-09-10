"""Tests for pydoclint plugin _build_command method.

The simplified plugin delegates most configuration to pydoclint's native
pyproject.toml reading. Only --quiet is managed by the plugin.
"""

from __future__ import annotations

from assertpy import assert_that

from lintro.tools.pydoclint.definition import PydoclintPlugin


def test_build_command_basic(pydoclint_plugin: PydoclintPlugin) -> None:
    """Build basic command with default options.

    Args:
        pydoclint_plugin: The PydoclintPlugin instance to test.
    """
    cmd = pydoclint_plugin._build_command()
    assert_that(cmd).contains("pydoclint")
    assert_that(cmd).contains("--quiet")


def test_build_command_quiet_enabled_by_default(
    pydoclint_plugin: PydoclintPlugin,
) -> None:
    """Build command with quiet enabled by default.

    Args:
        pydoclint_plugin: The PydoclintPlugin instance to test.
    """
    cmd = pydoclint_plugin._build_command()
    assert_that(cmd).contains("--quiet")


def test_build_command_with_quiet_false(pydoclint_plugin: PydoclintPlugin) -> None:
    """Build command with quiet disabled.

    Args:
        pydoclint_plugin: The PydoclintPlugin instance to test.
    """
    pydoclint_plugin.options["quiet"] = False
    cmd = pydoclint_plugin._build_command()

    assert_that(cmd).does_not_contain("--quiet")
