"""Tests for the golangci-lint plugin definition."""

from __future__ import annotations

from assertpy import assert_that

from lintro.enums.tool_type import ToolType
from lintro.tools.definitions.golangci_lint import GolangciLintPlugin


def test_definition_basics() -> None:
    """The definition exposes the expected identity and capabilities."""
    definition = GolangciLintPlugin().definition
    assert_that(definition.name).is_equal_to("golangci_lint")
    assert_that(definition.can_fix).is_true()
    assert_that(definition.tool_type).is_equal_to(ToolType.LINTER)
    assert_that(definition.file_patterns).contains("*.go")
    assert_that(definition.version_command).is_equal_to(
        ["golangci-lint", "version"],
    )


def test_definition_native_configs() -> None:
    """All golangci-lint config filenames are declared."""
    definition = GolangciLintPlugin().definition
    assert_that(definition.native_configs).contains(
        ".golangci.yml",
        ".golangci.yaml",
        ".golangci.toml",
        ".golangci.json",
    )


def test_doc_url_for_linter() -> None:
    """doc_url() builds a per-linter documentation anchor."""
    plugin = GolangciLintPlugin()
    assert_that(plugin.doc_url("errcheck")).is_equal_to(
        "https://golangci-lint.run/usage/linters/#errcheck",
    )


def test_doc_url_empty_code_returns_none() -> None:
    """doc_url() returns None when no code is supplied."""
    assert_that(GolangciLintPlugin().doc_url("")).is_none()
