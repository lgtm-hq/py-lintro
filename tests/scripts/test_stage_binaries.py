"""Tests for the npm binary staging script.

Covers direct-layout resolution, rejection of files outside that layout,
and the full staging pass against a temporary artifacts tree.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "ci" / "npm" / "stage_binaries.py"


def _load_module() -> ModuleType:
    """Import stage_binaries without executing its CLI.

    Returns:
        The loaded module.
    """
    spec = importlib.util.spec_from_file_location("stage_binaries", _SCRIPT)
    assert_that(spec).is_not_none()
    assert spec is not None  # narrow type for mypy
    assert_that(spec.loader).is_not_none()
    assert spec.loader is not None  # narrow type for mypy
    module = importlib.util.module_from_spec(spec)
    sys.modules["stage_binaries"] = module
    spec.loader.exec_module(module)
    return module


def _write_binary(path: Path, content: bytes = b"#!/bin/sh\n") -> None:
    """Create a fake binary file.

    Args:
        path: Destination file path.
        content: File content to write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_find_binary_accepts_direct_layout(tmp_path: Path) -> None:
    """The <artifact>/<artifact> layout resolves to the binary."""
    mod = _load_module()
    direct = tmp_path / "lintro-linux-x64" / "lintro-linux-x64"
    _write_binary(direct)

    found = mod._find_binary(tmp_path, "lintro-linux-x64")

    assert_that(str(found)).is_equal_to(str(direct))


def test_find_binary_ignores_files_outside_direct_layout(tmp_path: Path) -> None:
    """A matching filename elsewhere in the tree is not accepted."""
    mod = _load_module()
    _write_binary(tmp_path / "stray" / "lintro-macos-arm64")

    found = mod._find_binary(tmp_path, "lintro-macos-arm64")

    assert_that(found).is_none()


def test_find_binary_returns_none_when_missing(tmp_path: Path) -> None:
    """No candidate files yields None."""
    mod = _load_module()

    found = mod._find_binary(tmp_path, "lintro-macos-x86_64")

    assert_that(found).is_none()


def test_stage_binaries_stages_all_platforms(tmp_path: Path) -> None:
    """All mapped artifacts are copied into npm/<platform>/bin/lintro."""
    mod = _load_module()
    artifacts = tmp_path / "artifacts"
    for artifact_name in mod.BINARY_MAP:
        _write_binary(artifacts / artifact_name / artifact_name)
    npm_dir = tmp_path / "npm"

    staged = mod.stage_binaries(artifacts, npm_dir=npm_dir)

    assert_that(staged).is_equal_to(list(mod.BINARY_MAP.values()))
    for platform_key in mod.BINARY_MAP.values():
        dest = npm_dir / platform_key / "bin" / "lintro"
        assert_that(dest.is_file()).is_true()


def test_stage_binaries_missing_artifact_raises(tmp_path: Path) -> None:
    """A missing artifact fails loudly with FileNotFoundError."""
    mod = _load_module()
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()

    with pytest.raises(FileNotFoundError) as exc_info:
        mod.stage_binaries(artifacts, npm_dir=tmp_path / "npm")

    assert_that(str(exc_info.value)).contains("Missing binary artifact")


def test_main_reports_missing_artifact_as_failure(tmp_path: Path) -> None:
    """The CLI exits non-zero when an artifact only exists off-layout."""
    mod = _load_module()
    artifacts = tmp_path / "artifacts"
    for artifact_name in mod.BINARY_MAP:
        _write_binary(artifacts / artifact_name / artifact_name)
    # Move one binary out of the direct layout; it must not be picked up.
    (artifacts / "lintro-macos-arm64" / "lintro-macos-arm64").unlink()
    _write_binary(artifacts / "elsewhere" / "lintro-macos-arm64")

    exit_code = mod.main(["--artifacts-dir", str(artifacts)])

    assert_that(exit_code).is_equal_to(1)
