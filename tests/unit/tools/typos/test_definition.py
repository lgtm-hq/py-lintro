"""Tests for the typos plugin definition and metadata."""

from __future__ import annotations

from pathlib import Path

from assertpy import assert_that

from lintro.enums.tool_type import ToolType
from lintro.tools.definitions.typos import TYPOS_DEFAULT_TIMEOUT, TyposPlugin


def test_definition_basic_metadata(typos_plugin: TyposPlugin) -> None:
    """The definition exposes the expected identity and capabilities.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
    """
    definition = typos_plugin.definition

    assert_that(definition.name).is_equal_to("typos")
    assert_that(definition.can_fix).is_true()
    assert_that(definition.tool_type).is_equal_to(ToolType.LINTER)


def test_definition_file_patterns_match_all(typos_plugin: TyposPlugin) -> None:
    """Typos inspects all text files via a catch-all pattern.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
    """
    assert_that(typos_plugin.definition.file_patterns).is_equal_to(["*"])


def test_definition_native_configs(typos_plugin: TyposPlugin) -> None:
    """The definition advertises typos' native config filenames.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
    """
    assert_that(typos_plugin.definition.native_configs).contains(
        "typos.toml",
        ".typos.toml",
        "_typos.toml",
    )


def test_default_timeout_option(typos_plugin: TyposPlugin) -> None:
    """The default timeout option is applied.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
    """
    assert_that(typos_plugin.options.get("timeout")).is_equal_to(TYPOS_DEFAULT_TIMEOUT)


def test_build_command_uses_json_format(typos_plugin: TyposPlugin) -> None:
    """The base command requests JSON output for reliable parsing.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
    """
    cmd = typos_plugin._build_command()

    assert_that(cmd).is_equal_to(["typos", "--format", "json", "--force-exclude"])


def test_build_command_forces_excludes(typos_plugin: TyposPlugin) -> None:
    """``--force-exclude`` keeps .typos.toml excludes effective.

    typos skips its ignore rules for paths named on the command line unless
    this flag is set, and lintro always passes an explicit file list.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
    """
    assert_that(typos_plugin._build_command()).contains("--force-exclude")


def test_text_files_drops_binary_files(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """Binary files are filtered out before typos is invoked.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    (tmp_path / "notes.txt").write_text("plain text\n")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00binary")

    kept = typos_plugin._text_files(
        files=["notes.txt", "logo.png"],
        cwd=str(tmp_path),
    )

    assert_that(kept).is_equal_to(["notes.txt"])
