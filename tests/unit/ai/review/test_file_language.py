"""Tests for changed-file language tagging via identify."""

from __future__ import annotations

from pathlib import Path

from assertpy import assert_that

from lintro.ai.review.file_language import languages_for_path, languages_for_paths


def test_languages_for_path_tags_rust_source() -> None:
    """A .rs path resolves to the rust identify tag."""
    assert_that(languages_for_path(path="src/lib.rs")).contains("rust")


def test_languages_for_path_handles_nested_and_windows_paths() -> None:
    """Nested and backslash paths resolve by basename."""
    assert_that(languages_for_path(path="a/b/c/app.tsx")).contains("tsx")
    assert_that(languages_for_path(path="a\\b\\service.py")).contains("python")


def test_languages_for_path_unknown_extension_is_empty() -> None:
    """Unknown extensions yield no tags."""
    assert_that(languages_for_path(path="data.unknownext")).is_empty()


def test_languages_for_paths_unions_tags() -> None:
    """Combined tags include every input language."""
    tags = languages_for_paths(paths=["main.go", "lib.rs", "app.py"])

    assert_that(tags).contains("go")
    assert_that(tags).contains("rust")
    assert_that(tags).contains("python")


def test_languages_for_paths_empty_input_is_empty() -> None:
    """No paths means no language tags."""
    assert_that(languages_for_paths(paths=[])).is_empty()


def test_languages_for_path_tags_extensionless_scripts() -> None:
    """Extensionless scripts under bin/ or scripts/ receive a shell tag."""
    assert_that(languages_for_path(path="bin/lintro")).contains("shell")
    assert_that(languages_for_path(path="scripts/deploy")).contains("shell")


def test_languages_for_path_tags_extensionful_scripts_as_shell() -> None:
    """Shell extensions under top-level scripts/ receive a shell tag."""
    tags = languages_for_path(path="scripts/deploy.sh")

    assert_that(tags).contains("shell")


def test_languages_for_path_does_not_tag_nested_or_non_script_paths() -> None:
    """Nested bin/scripts paths and script-dir docs skip shell tagging."""
    assert_that(languages_for_path(path="src/bin/parser.py")).does_not_contain("shell")
    assert_that(languages_for_path(path="src/scripts/helpers.ts")).does_not_contain(
        "shell",
    )
    assert_that(languages_for_path(path="scripts/deploy.py")).does_not_contain("shell")
    assert_that(languages_for_path(path="scripts/README.md")).does_not_contain("shell")


def test_languages_for_path_reads_extensionless_shebang_with_repo_root(
    tmp_path: Path,
) -> None:
    """Extensionless scripts resolve interpreter tags from shebangs."""
    script = tmp_path / "bin" / "lintro"
    script.parent.mkdir(parents=True)
    script.write_text("#!/usr/bin/env python3\nprint('hi')\n", encoding="utf-8")
    script.chmod(0o755)

    tags = languages_for_path(path="bin/lintro", repo_root=tmp_path)

    assert_that(tags).contains("python")
    assert_that(tags).contains("shell")
