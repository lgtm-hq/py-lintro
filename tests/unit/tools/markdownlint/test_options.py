"""Unit tests for markdownlint plugin options and command building."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import patch

from assertpy import assert_that

from lintro.tools.core.command_builders import pinned_npm_spec
from lintro.tools.definitions.markdownlint import MarkdownlintPlugin


def _which_only(*available: str) -> Callable[..., str | None]:
    """Build a ``shutil.which`` stub that only finds the named executables.

    Args:
        *available: Executable names that should resolve.

    Returns:
        Callable usable as a ``shutil.which`` replacement.
    """

    def _which(name: str, *_args: object, **_kwargs: object) -> str | None:
        return f"/usr/local/bin/{name}" if name in available else None

    return _which


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


def test_get_markdownlint_command_prefers_path_binary(
    markdownlint_plugin: MarkdownlintPlugin,
    no_local_node_install: None,
) -> None:
    """Command uses the NodeJSBuilder PATH hit, not a plugin-local shutil.which.

    Production delegates to ``_get_executable_command`` (#1811). A PATH install
    is the absolute resolved path, not the bare binary name.

    Args:
        markdownlint_plugin: The MarkdownlintPlugin instance to test.
        no_local_node_install: Fixture removing any project-local Node install.
    """
    with patch("shutil.which", _which_only("markdownlint-cli2")):
        cmd = markdownlint_plugin._get_markdownlint_command()

    assert_that(cmd).is_equal_to(["/usr/local/bin/markdownlint-cli2"])


def test_get_markdownlint_command_falls_back_to_pinned_bunx(
    markdownlint_plugin: MarkdownlintPlugin,
    no_local_node_install: None,
) -> None:
    """Command falls back to version-pinned bunx when PATH has no binary.

    Args:
        markdownlint_plugin: The MarkdownlintPlugin instance to test.
        no_local_node_install: Fixture removing any project-local Node install.
    """
    with patch("shutil.which", _which_only("bunx")):
        cmd = markdownlint_plugin._get_markdownlint_command()

    assert_that(cmd).is_equal_to(["bunx", pinned_npm_spec("markdownlint-cli2")])


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
