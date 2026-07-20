"""Tests for scripts/ci/verify-manifest-tools.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from assertpy import assert_that


def _load_verify_manifest_tools_module() -> ModuleType:
    """Load verify-manifest-tools.py as a module for unit testing."""
    script_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "ci"
        / "verify-manifest-tools.py"
    )
    spec = importlib.util.spec_from_file_location(
        "verify_manifest_tools",
        script_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load verify-manifest-tools.py module")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tool_command_returns_manifest_version_command() -> None:
    """verify-manifest-tools should return the entry's version_command verbatim."""
    module = _load_verify_manifest_tools_module()

    # Access private function for testing - module loaded dynamically via importlib
    tool_command_fn = module._tool_command  # noqa: SLF001
    cmd = tool_command_fn(
        "astro_check",
        {
            "name": "astro_check",
            "install": {"type": "npm", "package": "astro", "bin": "astro"},
            "version_command": ["astro", "--version"],
        },
    )

    assert_that(cmd).is_equal_to(["astro", "--version"])


def test_tool_command_rejects_missing_version_command() -> None:
    """verify-manifest-tools should raise when version_command is absent."""
    module = _load_verify_manifest_tools_module()

    tool_command_fn = module._tool_command  # noqa: SLF001
    assert_that(tool_command_fn).raises(ValueError).when_called_with(
        "astro_check",
        {"name": "astro_check", "install": {"type": "npm"}},
    )


def test_clippy_versions_match_ignores_unobservable_patch() -> None:
    """Clippy matches at major.minor since its binary never reports a patch.

    `cargo clippy --version` emits `clippy 0.1.<minor>`, which the parser maps
    to `1.<minor>.0`. A manifest that pins a real toolchain patch (e.g. 1.97.1)
    must still match that synthesized `.0`.
    """
    module = _load_verify_manifest_tools_module()

    versions_match = module._versions_match  # noqa: SLF001
    assert_that(versions_match("clippy", "1.97.1", "1.97.0")).is_true()
    assert_that(versions_match("clippy", "1.97.0", "1.97.0")).is_true()


def test_clippy_versions_mismatch_on_minor_drift() -> None:
    """Clippy still fails when the observable major.minor genuinely drifts."""
    module = _load_verify_manifest_tools_module()

    versions_match = module._versions_match  # noqa: SLF001
    assert_that(versions_match("clippy", "1.97.1", "1.96.0")).is_false()


def test_non_clippy_versions_require_exact_match() -> None:
    """Non-clippy tools keep strict, patch-level version equality."""
    module = _load_verify_manifest_tools_module()

    versions_match = module._versions_match  # noqa: SLF001
    assert_that(versions_match("ruff", "1.97.1", "1.97.1")).is_true()
    assert_that(versions_match("ruff", "1.97.1", "1.97.0")).is_false()
