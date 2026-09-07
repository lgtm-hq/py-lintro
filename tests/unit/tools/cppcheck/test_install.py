"""Installer and image wiring tests for cppcheck."""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 - fixed argv, shell=False, controlled test input
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro._tool_versions import get_tool_version

_REPO_ROOT = Path(__file__).resolve().parents[4]
_INSTALL_TOOLS = _REPO_ROOT / "scripts" / "utils" / "install-tools.sh"
_DOCKERFILE = _REPO_ROOT / "Dockerfile"
_TOOLS_DOCKERFILE = _REPO_ROOT / "docker" / "tools.Dockerfile"


def _modern_bash() -> str | None:
    """Locate a bash new enough to run the installer.

    ``install-tools.sh`` uses associative arrays, so bash 3.2 (the system bash
    shipped by macOS) cannot run it.

    Returns:
        Path to a bash >= 4 interpreter, or None when only an older one exists.
    """
    bash = shutil.which("bash")
    if bash is None:
        return None
    probe = subprocess.run(  # nosec B603 - fixed argv in a controlled test
        [bash, "-c", "echo ${BASH_VERSINFO[0]}"],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    major = probe.stdout.strip()
    return bash if major.isdigit() and int(major) >= 4 else None


_BASH = _modern_bash()

requires_modern_bash = pytest.mark.skipif(
    _BASH is None,
    reason="install-tools.sh requires bash >= 4 (associative arrays)",
)


@requires_modern_bash
def test_dry_run_selects_cppcheck() -> None:
    """``--tools cppcheck`` reaches the cppcheck install block.

    The dry run proves the filter name the Dockerfile bridge passes actually
    selects a block, rather than being silently ignored.
    """
    assert _BASH is not None  # nosec B101 - guarded by requires_modern_bash
    version = get_tool_version("cppcheck")
    assert_that(version).is_not_none()

    result = subprocess.run(  # nosec B603 - fixed argv in a controlled test
        [_BASH, str(_INSTALL_TOOLS), "--dry-run", "--tools", "cppcheck"],
        capture_output=True,
        check=False,
        text=True,
        timeout=300,
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains(f"Would install cppcheck v{version}")


def test_supported_tools_lists_cppcheck() -> None:
    """``--tools cppcheck`` is accepted by the installer's validator.

    Scoped to the ``SUPPORTED_TOOLS`` array: a bare search of the whole script
    would match the install block and prove nothing about the validator.
    """
    script = _INSTALL_TOOLS.read_text(encoding="utf-8")
    supported_at = script.find("SUPPORTED_TOOLS=(")
    assert_that(supported_at).is_not_equal_to(-1)
    supported = script[supported_at : script.index(")", supported_at)]

    assert_that(supported).contains('"cppcheck"')


def test_install_block_fails_loudly() -> None:
    """Every failing install path in the cppcheck block exits non-zero."""
    script = _INSTALL_TOOLS.read_text(encoding="utf-8")
    block_at = script.find('if should_install "cppcheck"; then')
    assert_that(block_at).is_not_equal_to(-1)
    block_end = script.find("fi # cppcheck", block_at)
    assert_that(block_end).described_as("missing 'fi # cppcheck'").is_not_equal_to(-1)
    block = script[block_at:block_end]

    assert_that(block).contains("brew install cppcheck")
    assert_that(block).contains("install -y --no-install-recommends cppcheck")
    assert_that(block).contains("exit 1")


def test_install_block_enforces_the_manifest_minimum() -> None:
    """The installer rejects a distro package below ``min_version``.

    Every install path takes whatever the package manager ships, so the only
    guard against an old distro cppcheck is the post-install comparison. Below
    the floor lintro skips the tool at runtime, which would let setup finish
    green while C/C++ analysis never runs.
    """
    script = _INSTALL_TOOLS.read_text(encoding="utf-8")
    block_at = script.find('if should_install "cppcheck"; then')
    block = script[block_at : script.find("fi # cppcheck", block_at)]

    assert_that(block).contains('get_tool_min_version "cppcheck"')
    assert_that(block).contains(
        'version_ge "$cppcheck_installed" "$CPPCHECK_MIN_VERSION"',
    )
    assert_that(block).contains("is older than the required")


def test_verification_loop_includes_cppcheck() -> None:
    """``tools_to_verify`` names cppcheck, so ``--tools cppcheck`` verifies it.

    A missing array entry would silently drop verification while every other
    assertion here still passed.
    """
    script = _INSTALL_TOOLS.read_text(encoding="utf-8")
    verify_at = script.find("tools_to_verify=(")
    assert_that(verify_at).is_not_equal_to(-1)
    verify_array = script[verify_at : script.index(")", verify_at)]

    assert_that(verify_array).contains('"cppcheck"')


def test_tools_image_installs_and_verifies_the_binary() -> None:
    """``docker/tools.Dockerfile`` installs the package and proves it is on PATH."""
    text = _TOOLS_DOCKERFILE.read_text(encoding="utf-8")

    assert_that(text).contains("cppcheck --version")
    apt_at = text.find("apt-get install")
    assert_that(apt_at).is_not_equal_to(-1)
    assert_that(text[apt_at : text.index("\n\n", apt_at)]).contains("cppcheck")


def test_app_image_bridges_cppcheck_until_the_next_tools_digest() -> None:
    """The app image FROMs a digest-pinned tools base that predates this tool.

    Until that digest is republished with ``cppcheck`` on PATH, the app image
    must install it itself, or the manifest-vs-image gate
    (``scripts/ci/verify-image-manifest-tools.sh``) fails with exit code 127
    for ``cppcheck``. This bridge is a no-op once the pinned digest already
    carries the binary.
    """
    text = _DOCKERFILE.read_text(encoding="utf-8")
    bridge_at = text.find("install-tools.sh --docker --tools ")

    assert_that(bridge_at).is_not_equal_to(-1)
    bridge_line = text[bridge_at : text.index("\n", bridge_at)]
    assert_that(bridge_line).contains("cppcheck")


@requires_modern_bash
def test_installer_rejects_a_cppcheck_below_the_minimum(tmp_path: Path) -> None:
    """A too-old distro package fails the install rather than passing green.

    This is the failure the version gate exists for: below the floor lintro
    skips the tool at runtime, so an installer that accepted it would leave
    setup green with C/C++ analysis silently disabled. A stub binary reporting
    2.12.0 stands in for a stale distro package.

    Args:
        tmp_path: Temporary directory holding the stub binary.
    """
    assert _BASH is not None  # nosec B101 - guarded by requires_modern_bash
    stub = tmp_path / "cppcheck"
    stub.write_text('#!/bin/sh\necho "Cppcheck 2.12.0"\n', encoding="utf-8")
    stub.chmod(0o755)

    result = subprocess.run(  # nosec B603 - fixed argv in a controlled test
        [_BASH, str(_INSTALL_TOOLS), "--tools", "cppcheck"],
        capture_output=True,
        check=False,
        text=True,
        timeout=300,
        env={**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"},
    )

    assert_that(result.returncode).is_not_equal_to(0)
    assert_that(result.stdout + result.stderr).contains(
        "is older than the required v2.13.0",
    )


@requires_modern_bash
def test_installer_rejects_an_unparseable_cppcheck_version(tmp_path: Path) -> None:
    """An unreadable ``--version`` fails closed instead of being assumed good.

    Args:
        tmp_path: Temporary directory holding the stub binary.
    """
    assert _BASH is not None  # nosec B101 - guarded by requires_modern_bash
    stub = tmp_path / "cppcheck"
    stub.write_text("#!/bin/sh\necho 'no version here'\n", encoding="utf-8")
    stub.chmod(0o755)

    result = subprocess.run(  # nosec B603 - fixed argv in a controlled test
        [_BASH, str(_INSTALL_TOOLS), "--tools", "cppcheck"],
        capture_output=True,
        check=False,
        text=True,
        timeout=300,
        env={**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"},
    )

    assert_that(result.returncode).is_not_equal_to(0)
    assert_that(result.stdout + result.stderr).contains(
        "Could not determine cppcheck version",
    )
