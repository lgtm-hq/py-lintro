"""Tests for the macOS Nuitka build script."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BUILD_SCRIPT = _REPO_ROOT / "scripts" / "build" / "build_macos.py"


def _load_build_macos_module():
    """Import build_macos without executing its main entry point.

    Returns:
        Loaded build_macos module.
    """
    spec = importlib.util.spec_from_file_location("build_macos", _BUILD_SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_macos"] = module
    spec.loader.exec_module(module)
    return module


def test_build_nuitka_command_includes_manifest_json() -> None:
    """Nuitka command must bundle manifest.json for onefile runtime."""
    build_macos = _load_build_macos_module()

    cmd = build_macos.build_nuitka_command(arch="arm64")

    assert_that(cmd).contains("--include-package-data=lintro")
    assert_that(cmd).contains(
        "--include-data-files=lintro/tools/manifest.json=lintro/tools/manifest.json",
    )
