"""Tests for AI path utilities."""

from __future__ import annotations

import os

from assertpy import assert_that

from lintro.ai.paths import relative_path


class TestRelativePath:
    """Tests for relative_path utility."""

    def test_returns_relative_for_cwd_child(self):
        cwd = os.getcwd()
        abs_path = os.path.join(cwd, "src", "main.py")
        result = relative_path(abs_path)
        assert_that(result).is_equal_to(os.path.join("src", "main.py"))

    def test_returns_already_relative(self):
        result = relative_path("src/main.py")
        assert_that(result).is_equal_to("src/main.py")

    def test_returns_original_on_empty(self):
        result = relative_path("")
        assert_that(result).is_type_of(str)
