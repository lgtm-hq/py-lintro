"""Installer wiring tests for import-linter in ``install-tools.sh``."""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - fixed argv, shell=False, controlled test input
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro._tool_versions import get_tool_version

_REPO_ROOT = Path(__file__).resolve().parents[4]
_INSTALL_TOOLS = _REPO_ROOT / "scripts" / "utils" / "install-tools.sh"


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
    """Run the installer's dry-run path for import-linter only.

    Returns:
        The completed process, with stdout and stderr captured as text.
    """
    assert _BASH is not None  # nosec B101 - guarded by requires_modern_bash
    return subprocess.run(  # nosec B603 - fixed argv in a controlled test
        [_BASH, str(_INSTALL_TOOLS), "--dry-run", "--tools", "import-linter"],
        capture_output=True,
        check=False,
        text=True,
        timeout=300,
    )


@requires_modern_bash
def test_dry_run_installs_pinned_version() -> None:
    """A dry run installs the pin recorded for the ``import-linter`` package."""
    version = get_tool_version("import-linter")
    assert_that(version).is_not_none()

    result = _dry_run()

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains(f"Would install import-linter=={version}")


@requires_modern_bash
def test_dry_run_verifies_the_lint_imports_binary() -> None:
    """Verification targets ``lint-imports``, the binary the package ships."""
    result = _dry_run()

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Would verify lint-imports is available")


def test_supported_tools_lists_import_linter() -> None:
    """``--tools import-linter`` is accepted by the installer's validator."""
    script = _INSTALL_TOOLS.read_text(encoding="utf-8")

    assert_that(script).contains('"import-linter"')
