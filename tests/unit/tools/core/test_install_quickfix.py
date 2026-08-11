"""Tests for doctor quick-fix generation."""

from __future__ import annotations

from unittest.mock import patch

from assertpy import assert_that

from lintro.enums.install_context import InstallContext, PackageManager
from lintro.tools.core.install_quickfix import build_quick_fix
from lintro.tools.core.install_strategies.environment import InstallEnvironment
from lintro.tools.core.tool_registry import ManifestTool


def _env(*managers: PackageManager) -> InstallEnvironment:
    """Build an environment with the given package managers available.

    Args:
        *managers: Package managers to mark as present.

    Returns:
        InstallEnvironment instance.
    """
    return InstallEnvironment(
        install_context=InstallContext.PIP,
        available_managers=frozenset(managers),
    )


def _tool(name: str, install_type: str) -> ManifestTool:
    """Build a manifest tool of the given install type.

    Args:
        name: Tool name.
        install_type: Install type identifier.

    Returns:
        ManifestTool instance.
    """
    return ManifestTool(
        name=name,
        version="1.0.0",
        min_version="1.0.0",
        install_type=install_type,
        install_package=name,
        version_command=(name, "--version"),
    )


def test_quick_fix_lists_only_executable_tools() -> None:
    """Tools without an available package manager stay out of the command."""
    quick_fix = build_quick_fix(
        [_tool("ruff", "pip"), _tool("clippy", "rustup")],
        _env(PackageManager.UV),
    )

    assert_that(quick_fix.command).is_equal_to("lintro install ruff")
    assert_that([name for name, _reason in quick_fix.blocked]).is_equal_to(["clippy"])


def test_quick_fix_command_is_none_when_nothing_is_executable() -> None:
    """No runnable action means no quick-fix command is emitted at all."""
    quick_fix = build_quick_fix([_tool("clippy", "rustup")], _env())

    assert_that(quick_fix.command).is_none()
    assert_that(quick_fix.blocked).is_length(1)


def test_quick_fix_adds_upgrade_flag() -> None:
    """Upgrades are emitted with the --upgrade flag."""
    quick_fix = build_quick_fix(
        [_tool("ruff", "pip")],
        _env(PackageManager.UV),
        upgrade=True,
    )

    assert_that(quick_fix.command).is_equal_to("lintro install --upgrade ruff")


def test_quick_fix_never_repeats_a_known_invalid_command() -> None:
    """A tool whose command already failed is not suggested again."""
    quick_fix = build_quick_fix(
        [_tool("ruff", "pip"), _tool("golangci_lint", "binary")],
        _env(PackageManager.UV, PackageManager.BREW),
        known_invalid=["golangci_lint"],
    )

    assert_that(quick_fix.command).is_equal_to("lintro install ruff")
    assert_that([name for name, _reason in quick_fix.blocked]).contains("golangci_lint")


def test_quick_fix_blocks_tools_with_prose_only_hints() -> None:
    """A binary tool with no brew and no install script is a manual step."""
    with patch(
        "lintro.tools.core.install_quickfix.has_install_script",
        return_value=False,
    ):
        quick_fix = build_quick_fix([_tool("hadolint", "binary")], _env())

    assert_that(quick_fix.command).is_none()
    reasons = [reason for _name, reason in quick_fix.blocked]
    assert_that(reasons[0]).contains("https://")


def test_quick_fix_keeps_script_backed_binary_tools() -> None:
    """A binary tool the install script can handle stays executable."""
    with patch(
        "lintro.tools.core.install_quickfix.has_install_script",
        return_value=True,
    ):
        quick_fix = build_quick_fix([_tool("hadolint", "binary")], _env())

    assert_that(quick_fix.command).is_equal_to("lintro install hadolint")
