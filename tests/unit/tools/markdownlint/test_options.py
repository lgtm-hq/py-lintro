"""Unit tests for markdownlint plugin options and command building."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.tools.core.command_builders import pinned_npm_spec
from lintro.tools.markdownlint.definition import (
    MARKDOWNLINT_DEFAULT_TIMEOUT,
    MarkdownlintPlugin,
)
from tests.unit.tools.conftest import record_subprocess_argv


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
    """Default options include expected keys and values.

    Args:
        markdownlint_plugin: The MarkdownlintPlugin instance to test.
    """
    defaults = markdownlint_plugin.definition.default_options
    assert_that(defaults["timeout"]).is_equal_to(MARKDOWNLINT_DEFAULT_TIMEOUT)
    assert_that(defaults["line_length"]).is_none()


def test_set_options_line_length(markdownlint_plugin: MarkdownlintPlugin) -> None:
    """Set line_length option.

    Args:
        markdownlint_plugin: The MarkdownlintPlugin instance to test.
    """
    markdownlint_plugin.set_options(line_length=100)
    assert_that(markdownlint_plugin.options.get("line_length")).is_equal_to(100)


def test_check_uses_path_binary(
    markdownlint_plugin: MarkdownlintPlugin,
    no_local_node_install: None,
    tmp_path: Path,
) -> None:
    """check() argv uses the NodeJSBuilder PATH hit.

    Production delegates to ``_get_executable_command`` (#1811). A PATH install
    is the absolute resolved path, not the bare binary name.

    Args:
        markdownlint_plugin: The MarkdownlintPlugin instance to test.
        no_local_node_install: Fixture removing any project-local Node install.
        tmp_path: Temporary directory path for test files.
    """
    md_file = tmp_path / "README.md"
    md_file.write_text("# Title\n\nBody text.\n")
    commands: list[list[str]] = []

    with (
        patch("shutil.which", _which_only("markdownlint-cli2")),
        patch.object(
            markdownlint_plugin,
            "_run_subprocess",
            side_effect=record_subprocess_argv(commands),
        ),
    ):
        result = markdownlint_plugin.check([str(md_file)], {})

    assert_that(commands).is_length(1)
    assert_that(commands[0][0]).is_equal_to("/usr/local/bin/markdownlint-cli2")
    assert_that(result.success).is_true()


def test_check_falls_back_to_pinned_bunx(
    markdownlint_plugin: MarkdownlintPlugin,
    no_local_node_install: None,
    tmp_path: Path,
) -> None:
    """check() argv falls back to version-pinned bunx when PATH has no binary.

    Args:
        markdownlint_plugin: The MarkdownlintPlugin instance to test.
        no_local_node_install: Fixture removing any project-local Node install.
        tmp_path: Temporary directory path for test files.
    """
    md_file = tmp_path / "README.md"
    md_file.write_text("# Title\n\nBody text.\n")
    commands: list[list[str]] = []

    with (
        patch("shutil.which", _which_only("bunx")),
        patch.object(
            markdownlint_plugin,
            "_run_subprocess",
            side_effect=record_subprocess_argv(commands),
        ),
    ):
        result = markdownlint_plugin.check([str(md_file)], {})

    assert_that(commands).is_length(1)
    assert_that(commands[0][:2]).is_equal_to(
        ["bunx", pinned_npm_spec("markdownlint-cli2")],
    )
    assert_that(result.success).is_true()


def test_doc_url_lowercases_code(markdownlint_plugin: MarkdownlintPlugin) -> None:
    """doc_url lowercases the rule code.

    Args:
        markdownlint_plugin: The MarkdownlintPlugin instance to test.
    """
    url = markdownlint_plugin.doc_url("MD013")
    assert_that(url).contains("md013")
