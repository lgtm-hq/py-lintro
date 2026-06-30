"""Tests for changed-file language tagging via identify."""

from __future__ import annotations

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
