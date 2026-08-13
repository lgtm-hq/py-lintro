"""Tests for canonical-to-ecosystem package name resolution."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.enums.install_context import InstallContext, PackageManager
from lintro.tools.core.install_strategies import get_strategy
from lintro.tools.core.install_strategies.environment import InstallEnvironment
from lintro.tools.core.install_strategies.package_names import (
    BREW_FORMULA_NAMES,
    brew_formula_name,
    ecosystem_package_name,
    script_tool_name,
)
from lintro.tools.core.tool_registry import ManifestRegistry


def _brew_env() -> InstallEnvironment:
    """Build an environment where only Homebrew is available.

    Returns:
        InstallEnvironment with brew present.
    """
    return InstallEnvironment(
        install_context=InstallContext.HOMEBREW_FULL,
        available_managers=frozenset({PackageManager.BREW}),
    )


@pytest.mark.parametrize(
    ("tool_name", "expected"),
    [
        ("actionlint", "actionlint"),
        ("dotenv_linter", "dotenv-linter"),
        ("gitleaks", "gitleaks"),
        ("golangci_lint", "golangci-lint"),
        ("hadolint", "hadolint"),
        ("osv_scanner", "osv-scanner"),
        ("shellcheck", "shellcheck"),
        ("shfmt", "shfmt"),
        ("taplo", "taplo"),
        ("trufflehog", "trufflehog"),
        ("vale", "vale"),
        ("markdownlint", "markdownlint-cli2"),
    ],
    ids=[
        "tool=actionlint",
        "tool=dotenv_linter",
        "tool=gitleaks",
        "tool=golangci_lint",
        "tool=hadolint",
        "tool=osv_scanner",
        "tool=shellcheck",
        "tool=shfmt",
        "tool=taplo",
        "tool=trufflehog",
        "tool=vale",
        "tool=markdownlint",
    ],
)
def test_brew_formula_name_uses_ecosystem_spelling(
    tool_name: str,
    expected: str,
) -> None:
    """Homebrew formula names use the ecosystem spelling, not lintro's.

    Args:
        tool_name: Canonical lintro tool name.
        expected: Homebrew formula name.
    """
    assert_that(brew_formula_name(tool_name)).is_equal_to(expected)


def test_brew_formula_name_hyphenates_unknown_tools() -> None:
    """Unmapped canonical names still fall back to a hyphenated formula."""
    assert_that(brew_formula_name("some_new_tool")).is_equal_to("some-new-tool")


def test_brew_formula_name_prefers_explicit_map_over_package() -> None:
    """An explicit formula mapping wins over the manifest package name."""
    assert_that(
        brew_formula_name("markdownlint", "markdownlint-cli2-custom"),
    ).is_equal_to("markdownlint-cli2")


def test_every_binary_tool_has_an_explicit_brew_formula() -> None:
    """Binary tools must declare their formula rather than fall through."""
    registry = ManifestRegistry.load()
    binary_tools = [
        t.name
        for t in registry.all_tools(include_dev=True)
        if t.install_type == "binary"
    ]

    missing = [name for name in binary_tools if name not in BREW_FORMULA_NAMES]

    assert_that(missing).is_empty()


def test_no_manifest_tool_resolves_to_an_underscore_package() -> None:
    """No tool may silently install under lintro's underscore spelling."""
    registry = ManifestRegistry.load()

    implicit_underscores = [
        tool.name
        for tool in registry.all_tools(include_dev=True)
        if "_" in ecosystem_package_name(tool.name, tool.install_package)
        and not (tool.install_package and "_" in tool.install_package)
    ]

    assert_that(implicit_underscores).is_empty()


def test_ecosystem_package_name_prefers_manifest_override() -> None:
    """The manifest package override wins over the derived name."""
    assert_that(ecosystem_package_name("tsc", "typescript")).is_equal_to("typescript")


def test_script_tool_name_hyphenates() -> None:
    """install-tools.sh identifiers use hyphens."""
    assert_that(script_tool_name("golangci_lint")).is_equal_to("golangci-lint")


def test_binary_install_hint_uses_brew_formula_name() -> None:
    """Regression: brew must not be asked to install ``golangci_lint``."""
    strategy = get_strategy("binary")
    assert strategy is not None  # narrow type for mypy

    hint = strategy.install_hint(_brew_env(), "golangci_lint", "1.0.0", None, None)

    assert_that(hint).is_equal_to("brew install golangci-lint")


def test_binary_upgrade_hint_uses_brew_formula_name() -> None:
    """Upgrade hints use the same formula mapping as install hints."""
    strategy = get_strategy("binary")
    assert strategy is not None  # narrow type for mypy

    hint = strategy.upgrade_hint(_brew_env(), "golangci_lint", "1.0.0", None, None)

    assert_that(hint).is_equal_to("brew upgrade golangci-lint")
