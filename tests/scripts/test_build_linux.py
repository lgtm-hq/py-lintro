"""Tests for the Linux Nuitka build script."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LINUX_BUILD_SCRIPT = _REPO_ROOT / "scripts" / "build" / "build_linux.py"
_MACOS_BUILD_SCRIPT = _REPO_ROOT / "scripts" / "build" / "build_macos.py"
_VERIFY_SCRIPT = _REPO_ROOT / "scripts" / "build" / "verify_built_binary.sh"
_REVIEW_DRIVER = _REPO_ROOT / "scripts" / "build" / "drive_interactive_review.py"
_FAKE_CLAUDE = _REPO_ROOT / "scripts" / "build" / "fixtures" / "fake-claude" / "claude"
_FAKE_RUFF = _REPO_ROOT / "scripts" / "build" / "fixtures" / "fake-ruff" / "ruff"


def _load_build_linux_module() -> ModuleType:
    """Import build_linux without executing its main entry point.

    Returns:
        Loaded build_linux module.
    """
    spec = importlib.util.spec_from_file_location(
        "build_linux_wiring",
        _LINUX_BUILD_SCRIPT,
    )
    assert spec is not None and spec.loader is not None  # narrow type for mypy
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_linux_wiring"] = module
    spec.loader.exec_module(module)
    return module


def _load_macos_module() -> ModuleType:
    """Import build_macos without executing its main entry point.

    Returns:
        Loaded build_macos module.
    """
    spec = importlib.util.spec_from_file_location(
        "build_macos_lockstep",
        _MACOS_BUILD_SCRIPT,
    )
    assert spec is not None and spec.loader is not None  # narrow type for mypy
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_macos_lockstep"] = module
    spec.loader.exec_module(module)
    return module


def test_both_platforms_ship_the_same_packages_as_bytecode() -> None:
    """The bytecode policy must not diverge between the two build scripts.

    The list is copy-implemented per platform, following the scripts' existing
    ``INCLUDE_DATA_DIRS`` pattern; the per-file tests pin only today's two
    literals, so without this a package added to one side would ship compiled
    on the other and pass the whole suite.
    """
    linux_packages = _load_build_linux_module().BYTECODE_PACKAGES
    macos_packages = _load_macos_module().BYTECODE_PACKAGES

    assert_that(linux_packages).is_equal_to(macos_packages)
    assert_that(linux_packages).is_not_empty()


def test_build_nuitka_command_ships_lintro_and_pygments_as_bytecode() -> None:
    """Both packages must be requested as bytecode, not compiled to C (#2514).

    The Linux builds sit under the same 25-minute step cap as macOS; removing
    either flag returns them to a 16-21 minute compile.
    """
    build_linux = _load_build_linux_module()

    with patch.object(Path, "exists", return_value=True):
        cmd = build_linux.build_nuitka_command()

    assert_that(cmd).contains("--noinclude-custom-mode=lintro:bytecode")
    assert_that(cmd).contains("--noinclude-custom-mode=pygments:bytecode")


def test_bytecode_flags_follow_include_package_data() -> None:
    """Nuitka reads the mode after the package is included, so order matters."""
    build_linux = _load_build_linux_module()

    with patch.object(Path, "exists", return_value=True):
        cmd = build_linux.build_nuitka_command()

    include_index = cmd.index("--include-package-data=lintro")
    bytecode_index = cmd.index("--noinclude-custom-mode=lintro:bytecode")
    assert_that(bytecode_index).is_greater_than(include_index)


def test_verify_step_drives_the_committed_review_fixtures() -> None:
    """The verify step must reach the driver, and the fixtures must be runnable.

    A rename that leaves ``verify_built_binary.sh`` calling a missing driver
    would only fail on a release runner, long after the change merged.
    """
    verify_source = _VERIFY_SCRIPT.read_text(encoding="utf-8")

    assert_that(verify_source).contains(_REVIEW_DRIVER.name)
    assert_that(verify_source).contains("mcp")
    for fixture in (_REVIEW_DRIVER, _FAKE_CLAUDE, _FAKE_RUFF):
        assert_that(fixture.is_file()).is_true()
        assert_that(os.access(fixture, os.X_OK)).is_true()


def test_both_platforms_bundle_the_same_sdk_packages_and_metadata() -> None:
    """The SDK bundle policy must not diverge between the two build scripts."""
    build_linux = _load_build_linux_module()
    build_macos = _load_macos_module()

    assert_that(build_linux.MCP_SDK_PACKAGES).is_equal_to(
        build_macos.MCP_SDK_PACKAGES,
    )
    assert_that(build_linux.INCLUDE_DISTRIBUTION_METADATA).is_equal_to(
        build_macos.INCLUDE_DISTRIBUTION_METADATA,
    )
    assert_that(build_linux.MCP_SDK_PACKAGES).contains("mcp", "mcp_types")


def test_build_nuitka_command_compiles_the_package_not_a_file_inside_it() -> None:
    """Nuitka must get the ``lintro`` package in ``-m`` mode (#2577).

    See the macOS test of the same name; the Linux binaries crashed the same
    way for the same reason.
    """
    build_linux = _load_build_linux_module()
    package_dir = str(build_linux.PROJECT_ROOT / "lintro")

    with patch.object(Path, "exists", return_value=True):
        cmd = build_linux.build_nuitka_command()

    assert_that(cmd).contains("--python-flag=-m")
    assert_that(cmd[-1]).is_equal_to(package_dir)
    positional = [arg for arg in cmd[3:] if not arg.startswith("--")]
    inside_package = [arg for arg in positional if arg.startswith(package_dir + os.sep)]
    assert_that(inside_package).is_empty()


def test_build_nuitka_command_bundles_the_mcp_sdk() -> None:
    """The SDK, its metadata and its bytecode stack are all requested (#2577)."""
    build_linux = _load_build_linux_module()

    with patch.object(Path, "exists", return_value=True):
        cmd = build_linux.build_nuitka_command()

    assert_that(cmd).contains(
        "--include-package=mcp",
        "--include-package=mcp_types",
        "--include-distribution-metadata=httpx2",
        "--noinclude-custom-mode=mcp:bytecode",
        "--noinclude-custom-mode=pydantic:bytecode",
        "--noinclude-custom-mode=starlette:bytecode",
        "--noinclude-custom-mode=anyio:bytecode",
    )
