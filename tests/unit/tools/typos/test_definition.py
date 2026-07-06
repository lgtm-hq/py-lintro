"""Tests for the typos plugin definition and metadata."""

from __future__ import annotations

from assertpy import assert_that

from lintro.enums.tool_type import ToolType
from lintro.tools.definitions.typos import TYPOS_DEFAULT_TIMEOUT, TyposPlugin


def test_definition_basic_metadata(typos_plugin: TyposPlugin) -> None:
    """The definition exposes the expected identity and capabilities."""
    definition = typos_plugin.definition

    assert_that(definition.name).is_equal_to("typos")
    assert_that(definition.can_fix).is_true()
    assert_that(definition.tool_type).is_equal_to(ToolType.LINTER)


def test_definition_file_patterns_match_all(typos_plugin: TyposPlugin) -> None:
    """Typos inspects all text files via a catch-all pattern."""
    assert_that(typos_plugin.definition.file_patterns).is_equal_to(["*"])


def test_definition_native_configs(typos_plugin: TyposPlugin) -> None:
    """The definition advertises typos' native config filenames."""
    assert_that(typos_plugin.definition.native_configs).contains(
        "typos.toml",
        ".typos.toml",
        "_typos.toml",
    )


def test_default_timeout_option(typos_plugin: TyposPlugin) -> None:
    """The default timeout option is applied."""
    assert_that(typos_plugin.options.get("timeout")).is_equal_to(TYPOS_DEFAULT_TIMEOUT)


def test_build_command_uses_json_format(typos_plugin: TyposPlugin) -> None:
    """The base command requests JSON output for reliable parsing."""
    cmd = typos_plugin._build_command()

    assert_that(cmd).is_equal_to(["typos", "--format", "json"])
