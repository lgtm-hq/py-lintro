"""Unit tests for cppcheck plugin options and definition."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.enums.tool_type import ToolType
from lintro.tools.definitions.cppcheck import CppcheckPlugin


def test_definition_metadata(cppcheck_plugin: CppcheckPlugin) -> None:
    """The definition exposes the expected metadata.

    Args:
        cppcheck_plugin: The plugin under test.
    """
    definition = cppcheck_plugin.definition
    assert_that(definition.name).is_equal_to("cppcheck")
    assert_that(definition.can_fix).is_false()
    assert_that(definition.tool_type).is_equal_to(
        ToolType.LINTER | ToolType.SECURITY,
    )
    assert_that(definition.file_patterns).contains("*.c", "*.cpp", "*.h")
    assert_that(definition.version_command).is_equal_to(["cppcheck", "--version"])


def test_set_options_updates_enable(cppcheck_plugin: CppcheckPlugin) -> None:
    """Setting the enable option updates the stored value.

    Args:
        cppcheck_plugin: The plugin under test.
    """
    cppcheck_plugin.set_options(enable="warning")
    assert_that(cppcheck_plugin.options.get("enable")).is_equal_to("warning")


def test_set_options_inconclusive_and_std(cppcheck_plugin: CppcheckPlugin) -> None:
    """Boolean and string options are stored correctly.

    Args:
        cppcheck_plugin: The plugin under test.
    """
    cppcheck_plugin.set_options(inconclusive=True, std="c11")
    assert_that(cppcheck_plugin.options.get("inconclusive")).is_true()
    assert_that(cppcheck_plugin.options.get("std")).is_equal_to("c11")


def test_set_options_suppress_list(cppcheck_plugin: CppcheckPlugin) -> None:
    """Suppress accepts a list and reaches the built command.

    Args:
        cppcheck_plugin: The plugin under test.
    """
    cppcheck_plugin.set_options(suppress=["missingInclude", "unusedFunction"])
    cmd = cppcheck_plugin._build_command(files=["a.c"])
    assert_that(cmd).contains("--suppress=missingInclude")
    assert_that(cmd).contains("--suppress=unusedFunction")


def test_set_options_inline_suppr_flag(cppcheck_plugin: CppcheckPlugin) -> None:
    """The inline-suppr flag is added when enabled.

    Args:
        cppcheck_plugin: The plugin under test.
    """
    cppcheck_plugin.set_options(inline_suppr=True)
    assert_that(cppcheck_plugin._build_command(files=["a.c"])).contains(
        "--inline-suppr",
    )


def test_set_options_rejects_bad_type(cppcheck_plugin: CppcheckPlugin) -> None:
    """A non-boolean inconclusive value raises ValueError.

    Args:
        cppcheck_plugin: The plugin under test.
    """
    with pytest.raises(ValueError):
        cppcheck_plugin.set_options(inconclusive="yes")


def test_doc_url_returns_manual(cppcheck_plugin: CppcheckPlugin) -> None:
    """doc_url returns the manual URL for a code and None for empty input.

    Args:
        cppcheck_plugin: The plugin under test.
    """
    assert_that(cppcheck_plugin.doc_url("uninitvar")).contains("cppcheck")
    assert_that(cppcheck_plugin.doc_url("")).is_none()
