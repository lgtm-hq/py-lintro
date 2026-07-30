"""Integration tests for the golangci-lint plugin check command."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

# golangci-lint builds the module before linting, so both the linter and a Go
# toolchain must be present. Skip the whole module otherwise.
pytestmark = pytest.mark.skipif(
    shutil.which("golangci-lint") is None or shutil.which("go") is None,
    reason="golangci-lint or go toolchain not installed",
)


def test_check_module_with_violations(
    get_plugin: Callable[[str], BaseToolPlugin],
    golangci_violation_module: str,
) -> None:
    """golangci-lint check detects issues in a module with violations.

    Runs the plugin on the sample module that deliberately triggers errcheck
    and ineffassign, and verifies issues are reported.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        golangci_violation_module: Path to the violation module directory.
    """
    plugin = get_plugin("golangci_lint")
    result = plugin.check([golangci_violation_module], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("golangci_lint")
    assert_that(result.issues_count).is_greater_than(0)


def test_check_clean_module(
    get_plugin: Callable[[str], BaseToolPlugin],
    golangci_clean_module: str,
) -> None:
    """golangci-lint check passes on a violation-free module.

    Runs the plugin on a minimal module that triggers none of the enabled
    linters and verifies no issues are reported.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        golangci_clean_module: Path to the clean module directory.
    """
    plugin = get_plugin("golangci_lint")
    result = plugin.check([golangci_clean_module], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("golangci_lint")
    assert_that(result.issues_count).is_equal_to(0)
