"""Unit tests for clippy plugin options and helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.tools.definitions.clippy import ClippyPlugin, _find_cargo_root


def test_default_options(clippy_plugin: ClippyPlugin) -> None:
    """Default options include timeout.

    Args:
        clippy_plugin: The ClippyPlugin instance to test.
    """
    defaults = clippy_plugin.definition.default_options
    assert_that(defaults).contains_key("timeout")


def test_set_options_timeout(clippy_plugin: ClippyPlugin) -> None:
    """Set timeout option.

    Args:
        clippy_plugin: The ClippyPlugin instance to test.
    """
    clippy_plugin.set_options(timeout=60)
    assert_that(clippy_plugin.options.get("timeout")).is_equal_to(60)


def test_set_options_invalid_timeout(clippy_plugin: ClippyPlugin) -> None:
    """Raise ValueError for a non-positive timeout.

    Args:
        clippy_plugin: The ClippyPlugin instance to test.
    """
    with pytest.raises(ValueError, match="timeout must be"):
        clippy_plugin.set_options(timeout=-1)


def test_doc_url_formats_lint_name(clippy_plugin: ClippyPlugin) -> None:
    """doc_url formats the Clippy lint name into a URL.

    Args:
        clippy_plugin: The ClippyPlugin instance to test.
    """
    url = clippy_plugin.doc_url("needless_return")
    assert_that(url).contains("needless_return")


def test_doc_url_returns_none_for_empty_code(clippy_plugin: ClippyPlugin) -> None:
    """doc_url returns None when no code is given.

    Args:
        clippy_plugin: The ClippyPlugin instance to test.
    """
    assert_that(clippy_plugin.doc_url("")).is_none()


def test_find_cargo_root_locates_manifest(tmp_path: Path) -> None:
    """Locate the nearest Cargo.toml for a given file path.

    Args:
        tmp_path: Temporary directory path for test files.
    """
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "demo"\n')
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    rs_file = src_dir / "main.rs"
    rs_file.write_text("fn main() {}\n")

    root = _find_cargo_root([str(rs_file)])

    assert_that(root).is_equal_to(tmp_path.resolve())


def test_find_cargo_root_returns_none_when_missing(tmp_path: Path) -> None:
    """Return None when no Cargo.toml is found.

    Args:
        tmp_path: Temporary directory path for test files.
    """
    rs_file = tmp_path / "main.rs"
    rs_file.write_text("fn main() {}\n")

    root = _find_cargo_root([str(rs_file)])

    assert_that(root).is_none()
