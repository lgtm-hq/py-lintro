"""Tests for AI path utilities."""

from __future__ import annotations

import os
from pathlib import Path

from assertpy import assert_that

from lintro.ai.paths import (
    relative_path,
    resolve_workspace_file,
    resolve_workspace_root,
    to_provider_path,
)


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


class TestWorkspacePathUtilities:
    """Tests for workspace boundary and provider path sanitization."""

    def test_resolve_workspace_root_from_config(self, tmp_path):
        config = tmp_path / ".lintro-config.yaml"
        config.write_text("ai:\n  enabled: true\n", encoding="utf-8")

        root = resolve_workspace_root(str(config))
        assert_that(root).is_equal_to(tmp_path.resolve())

    def test_resolve_workspace_file_accepts_inside_path(self, tmp_path):
        inside = tmp_path / "src" / "main.py"
        inside.parent.mkdir(parents=True)
        inside.write_text("x = 1\n", encoding="utf-8")

        resolved = resolve_workspace_file(str(inside), tmp_path)
        assert_that(resolved).is_equal_to(inside.resolve())

    def test_resolve_workspace_file_rejects_outside_path(self, tmp_path):
        outside = Path("/tmp/outside-lintro-paths.py")
        resolved = resolve_workspace_file(str(outside), tmp_path)
        assert_that(resolved).is_none()

    def test_to_provider_path_is_workspace_relative(self, tmp_path):
        file_path = tmp_path / "pkg" / "module.py"
        file_path.parent.mkdir(parents=True)
        file_path.write_text("x = 1\n", encoding="utf-8")

        provider_path = to_provider_path(str(file_path), tmp_path)
        assert_that(provider_path).is_equal_to(os.path.join("pkg", "module.py"))

    def test_to_provider_path_falls_back_without_leaking_absolute(self, tmp_path):
        provider_path = to_provider_path("/tmp/secret/main.py", tmp_path)
        assert_that(provider_path).is_equal_to("main.py")
