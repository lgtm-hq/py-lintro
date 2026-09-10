"""Installer and image wiring tests for pylint."""

from __future__ import annotations

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


def _dry_run() -> subprocess.CompletedProcess[str]:
    """Run the installer's dry-run path for pylint only.

    Returns:
        The completed process, with stdout and stderr captured as text.
    """
    assert _BASH is not None  # nosec B101 - guarded by requires_modern_bash
    return subprocess.run(  # nosec B603 - fixed argv in a controlled test
        [_BASH, str(_INSTALL_TOOLS), "--dry-run", "--tools", "pylint"],
        capture_output=True,
        check=False,
        text=True,
        timeout=300,
    )


@requires_modern_bash
def test_dry_run_installs_pinned_version() -> None:
    """A dry run installs the pin recorded for the ``pylint`` package."""
    version = get_tool_version("pylint")
    assert_that(version).is_not_none()

    result = _dry_run()

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains(f"Would install pylint=={version}")


@requires_modern_bash
def test_dry_run_verifies_the_pylint_binary() -> None:
    """Verification targets the ``pylint`` console script the package ships."""
    result = _dry_run()

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Would verify pylint is available")


def test_supported_tools_lists_pylint() -> None:
    """``--tools pylint`` is accepted by the installer's validator.

    Scoped to the ``SUPPORTED_TOOLS`` array: a bare search of the whole script
    would match the install block and prove nothing about the validator.
    """
    script = _INSTALL_TOOLS.read_text(encoding="utf-8")
    supported_at = script.find("SUPPORTED_TOOLS=(")
    assert_that(supported_at).is_not_equal_to(-1)
    supported = script[supported_at : script.index(")", supported_at)]

    assert_that(supported).contains('"pylint"')


def test_install_block_fails_loudly() -> None:
    """A failed pip install must exit rather than report success."""
    script = _INSTALL_TOOLS.read_text(encoding="utf-8")
    block_at = script.find('if should_install "pylint"; then')
    assert_that(block_at).is_not_equal_to(-1)
    block_end = script.find("fi # pylint", block_at)
    assert_that(block_end).described_as("missing 'fi # pylint'").is_not_equal_to(-1)
    block = script[block_at:block_end]

    assert_that(block).contains('install_python_package "pylint" "$PYLINT_VERSION"')
    assert_that(block).contains("Failed to install pylint")
    assert_that(block).contains("exit 1")


def test_verification_loop_includes_pylint() -> None:
    """``tools_to_verify`` names pylint, so ``--tools pylint`` verifies it.

    The package name and the console-script name are both ``pylint``, so no
    alias branch is needed here — but a missing array entry would silently
    drop verification, which is exactly what this asserts against.
    """
    script = _INSTALL_TOOLS.read_text(encoding="utf-8")
    verify_at = script.find("tools_to_verify=(")
    assert_that(verify_at).is_not_equal_to(-1)
    verify_array = script[verify_at : script.index(")", verify_at)]

    assert_that(verify_array).contains('"pylint"')


def test_tools_image_verifies_the_binary() -> None:
    """``docker/tools.Dockerfile`` proves ``pylint`` is on the image PATH."""
    text = _TOOLS_DOCKERFILE.read_text(encoding="utf-8")

    assert_that(text).contains("pylint --version")


def test_app_image_bridges_pylint_until_the_next_tools_digest() -> None:
    """The app image FROMs a digest-pinned tools base that predates this tool.

    Until that digest is republished with ``pylint`` on PATH, the app image
    must install it itself, or the manifest-vs-image gate
    (``scripts/ci/verify-image-manifest-tools.sh``) fails with exit code 127
    for ``pylint``. This bridge is a no-op once the pinned digest already
    carries the binary.
    """
    text = _DOCKERFILE.read_text(encoding="utf-8")
    bridge_at = text.find("install-tools.sh --docker --tools ")

    assert_that(bridge_at).is_not_equal_to(-1)
    bridge_line = text[bridge_at : text.index("\n", bridge_at)]
    assert_that(bridge_line).contains("pylint")
