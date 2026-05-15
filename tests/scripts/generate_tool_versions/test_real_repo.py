"""End-to-end smoke tests against the real repository."""

from __future__ import annotations

import subprocess
import sys

from assertpy import assert_that

from tests.scripts.generate_tool_versions.conftest import REPO_ROOT, SCRIPT_PATH


def test_generator_check_passes_against_real_repo() -> None:
    """Generator check mode passes against the real repo.

    Runs only ``--check`` so the test cannot repair drift before asserting.
    """
    check_rc = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert_that(check_rc.returncode).described_as(
        check_rc.stdout + check_rc.stderr,
    ).is_equal_to(0)


def test_generated_module_passes_black() -> None:
    """The generator's output is byte-equivalent to what black would produce.

    Guards against future emitter regressions that would make the formatter
    and the drift gate fight each other on every PR.
    """
    generated_path = REPO_ROOT / "lintro" / "_generated_versions.py"
    rc = subprocess.run(
        [sys.executable, "-m", "black", "--check", "--quiet", str(generated_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert_that(rc.returncode).described_as(rc.stdout + rc.stderr).is_equal_to(0)


def test_generated_module_passes_ruff() -> None:
    """The generator's output passes ruff without modification."""
    generated_path = REPO_ROOT / "lintro" / "_generated_versions.py"
    rc = subprocess.run(
        [sys.executable, "-m", "ruff", "check", str(generated_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert_that(rc.returncode).described_as(rc.stdout + rc.stderr).is_equal_to(0)
