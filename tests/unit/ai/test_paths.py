"""Tests for AI path utilities."""

from __future__ import annotations

import os

from assertpy import assert_that

from lintro.ai.paths import (
    relative_path,
    resolve_workspace_file,
    resolve_workspace_root,
    to_provider_path,
)


def test_paths_relative_for_cwd_child():
    """Verify absolute path under cwd is converted to a relative path."""
    cwd = os.getcwd()
    abs_path = os.path.join(cwd, "src", "main.py")
    result = relative_path(abs_path)
    assert_that(result).is_equal_to(os.path.join("src", "main.py"))


def test_paths_already_relative():
    """Verify an already-relative path is returned unchanged."""
    result = relative_path("src/main.py")
    assert_that(result).is_equal_to("src/main.py")


def test_paths_relative_on_empty():
    """Verify relative_path handles an empty string without raising."""
    result = relative_path("")
    assert_that(result).is_type_of(str)


def test_paths_resolve_workspace_root_from_config(tmp_path):
    """Verify workspace root is resolved as the parent directory of the config file."""
    config = tmp_path / ".lintro-config.yaml"
    config.write_text("ai:\n  enabled: true\n", encoding="utf-8")

    root = resolve_workspace_root(str(config))
    assert_that(root).is_equal_to(tmp_path.resolve())


def test_paths_resolve_workspace_file_accepts_inside(tmp_path):
    """Verify a file inside the workspace root resolves successfully."""
    inside = tmp_path / "src" / "main.py"
    inside.parent.mkdir(parents=True)
    inside.write_text("x = 1\n", encoding="utf-8")

    resolved = resolve_workspace_file(str(inside), tmp_path)
    assert_that(resolved).is_equal_to(inside.resolve())


def test_paths_resolve_workspace_file_rejects_outside(tmp_path):
    """Paths outside the workspace root are rejected."""
    outside = tmp_path.parent / "outside-lintro-paths.py"
    resolved = resolve_workspace_file(str(outside), tmp_path)
    assert_that(resolved).is_none()


def test_paths_to_provider_path_is_workspace_relative(tmp_path):
    """Verify to_provider_path returns a workspace-relative path."""
    file_path = tmp_path / "pkg" / "module.py"
    file_path.parent.mkdir(parents=True)
    file_path.write_text("x = 1\n", encoding="utf-8")

    provider_path = to_provider_path(str(file_path), tmp_path)
    assert_that(provider_path).is_equal_to(os.path.join("pkg", "module.py"))


def test_paths_to_provider_path_falls_back_without_leaking_absolute(tmp_path):
    """Absolute paths outside workspace fall back to filename only."""
    outside_path = str(tmp_path.parent / "secret" / "main.py")
    provider_path = to_provider_path(outside_path, tmp_path)
    assert_that(provider_path).is_equal_to("main.py")
