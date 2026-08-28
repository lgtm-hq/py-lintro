"""Tests for the macOS Nuitka build script."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BUILD_SCRIPT = _REPO_ROOT / "scripts" / "build" / "build_macos.py"


def _load_build_macos_module() -> ModuleType:
    """Import build_macos without executing its main entry point.

    Returns:
        Loaded build_macos module.
    """
    spec = importlib.util.spec_from_file_location("build_macos", _BUILD_SCRIPT)
    assert_that(spec).is_not_none()
    assert spec is not None  # narrow type for mypy
    assert_that(spec.loader).is_not_none()
    assert spec.loader is not None  # narrow type for mypy
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_macos"] = module
    spec.loader.exec_module(module)
    return module


def test_build_nuitka_command_includes_manifest_json() -> None:
    """Nuitka command must bundle manifest.json for onefile runtime."""
    build_macos = _load_build_macos_module()

    with patch.object(Path, "exists", return_value=True):
        cmd = build_macos.build_nuitka_command(arch="arm64")

    assert_that(cmd).contains("--include-package-data=lintro")
    assert_that(cmd).contains(
        "--include-data-files=lintro/tools/manifest.json=lintro/tools/manifest.json",
    )


def test_build_nuitka_command_raises_when_manifest_missing(tmp_path: Path) -> None:
    """Missing manifest.json must fail the build instead of omitting the flag."""
    build_macos = _load_build_macos_module()
    main_entry = tmp_path / "lintro" / "__main__.py"
    main_entry.parent.mkdir(parents=True)
    main_entry.write_text("", encoding="utf-8")

    with patch.object(build_macos, "PROJECT_ROOT", tmp_path):
        with pytest.raises(FileNotFoundError, match="manifest.json"):
            build_macos.build_nuitka_command(arch="arm64")


def test_build_binary_regenerates_artifacts_before_nuitka() -> None:
    """The build regenerates the version artifacts before assembling Nuitka args.

    Regeneration makes the binary build self-contained (#2179): the bundled
    ``manifest.json`` / ``_generated_versions.py`` / ``_builtin_index.py``
    come from the sources, not from checkout state.
    """
    build_macos = _load_build_macos_module()
    calls: list[str] = []

    def _record(*args: object, **kwargs: object) -> None:
        """Record the ordering of regeneration vs command assembly."""
        calls.append("regenerate")

    def _fake_command(**kwargs: object) -> list[str]:
        """Record command assembly and return a dummy argv.

        Args:
            **kwargs: Ignored keyword arguments.

        Returns:
            Placeholder Nuitka argv.
        """
        calls.append("command")
        return ["nuitka"]

    with (
        patch.object(build_macos, "regenerate_version_artifacts", _record),
        patch.object(build_macos, "build_nuitka_command", _fake_command),
        patch.object(
            build_macos.subprocess,
            "run",
            lambda *a, **k: type("R", (), {"returncode": 0})(),
        ),
    ):
        build_macos.build_macos_binary(arch="arm64")

    assert_that(calls[:2]).is_equal_to(["regenerate", "command"])


def test_regenerate_version_artifacts_invokes_both_generators() -> None:
    """Both generator scripts run with check=True from the project root."""
    build_macos = _load_build_macos_module()
    invoked: list[str] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> None:
        """Capture generator invocations.

        Args:
            cmd: The argv list passed to subprocess.run.
            **kwargs: Ignored keyword arguments.
        """
        invoked.append(Path(cmd[-1]).name)
        assert_that(kwargs.get("check")).is_true()

    with patch.object(build_macos.subprocess, "run", _fake_run):
        build_macos.regenerate_version_artifacts()

    assert_that(invoked).is_equal_to(
        ["generate-tool-versions.py", "generate-builtin-tool-index.py"],
    )
