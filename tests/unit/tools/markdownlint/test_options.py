"""Unit tests for markdownlint plugin options and command building."""

from __future__ import annotations

from unittest.mock import patch

from assertpy import assert_that

from lintro.tools.definitions.markdownlint import MarkdownlintPlugin


def test_default_options(markdownlint_plugin: MarkdownlintPlugin) -> None:
    """Default options include expected keys.

    Args:
        markdownlint_plugin: The MarkdownlintPlugin instance to test.
    """
    defaults = markdownlint_plugin.definition.default_options
    assert_that(defaults).contains_key("timeout")
    assert_that(defaults).contains_key("line_length")


def test_set_options_line_length(markdownlint_plugin: MarkdownlintPlugin) -> None:
    """Set line_length option.

    Args:
        markdownlint_plugin: The MarkdownlintPlugin instance to test.
    """
    markdownlint_plugin.set_options(line_length=100)
    assert_that(markdownlint_plugin.options.get("line_length")).is_equal_to(100)


def test_get_markdownlint_command_prefers_direct_binary(
    markdownlint_plugin: MarkdownlintPlugin,
) -> None:
    """Command uses direct markdownlint-cli2 binary when available.

    Args:
        markdownlint_plugin: The MarkdownlintPlugin instance to test.
    """
    with patch(
        "lintro.tools.definitions.markdownlint.shutil.which",
        return_value="/usr/local/bin/markdownlint-cli2",
    ):
        cmd = markdownlint_plugin._get_markdownlint_command()

    assert_that(cmd).is_equal_to(["markdownlint-cli2"])


def test_get_markdownlint_command_falls_back_to_bunx(
    markdownlint_plugin: MarkdownlintPlugin,
) -> None:
    """Command falls back to bunx when direct binary is missing.

    Args:
        markdownlint_plugin: The MarkdownlintPlugin instance to test.
    """

    def fake_which(name: str) -> str | None:
        return "/usr/local/bin/bunx" if name == "bunx" else None

    with patch(
        "lintro.tools.definitions.markdownlint.shutil.which",
        side_effect=fake_which,
    ):
        cmd = markdownlint_plugin._get_markdownlint_command()

    assert_that(cmd).is_equal_to(["bunx", "markdownlint-cli2"])


def test_doc_url_lowercases_code(markdownlint_plugin: MarkdownlintPlugin) -> None:
    """doc_url lowercases the rule code.

    Args:
        markdownlint_plugin: The MarkdownlintPlugin instance to test.
    """
    url = markdownlint_plugin.doc_url("MD013")
    assert_that(url).contains("md013")


def test_doc_url_returns_none_for_empty_code(
    markdownlint_plugin: MarkdownlintPlugin,
) -> None:
    """doc_url returns None when no code is given.

    Args:
        markdownlint_plugin: The MarkdownlintPlugin instance to test.
    """
    assert_that(markdownlint_plugin.doc_url("")).is_none()
