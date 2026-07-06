"""Integration tests for actionlint tool."""

from __future__ import annotations

import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from pathlib import Path

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.plugins import ToolRegistry

logger.remove()
logger.add(lambda msg: print(msg, end=""), level="INFO")


def actionlint_available() -> bool:
    """Return True if the `actionlint` binary is available on PATH.

    Returns:
        bool: True when `actionlint -version` succeeds, False otherwise.
    """
    try:
        proc = subprocess.run(  # nosec B603 B607 - fixed argv run against a real binary in a controlled test; binary name resolved from PATH, not attacker-controlled; shell=False, no user shell input
            ["actionlint", "-version"],
            capture_output=True,
            text=True,
        )
        return proc.returncode == 0
    except FileNotFoundError:
        return False


@pytest.mark.actionlint
def test_actionlint_available() -> None:
    """Skip the suite if actionlint is not present locally.

    Ensures local runs behave like CI (which always has actionlint in Docker),
    but do not fail when developers don't have actionlint installed.
    """
    if not actionlint_available():
        pytest.skip("actionlint not available")


SAMPLE_BAD = Path("test_samples/tools/config/github_actions/actionlint_violations.yml")


@pytest.mark.actionlint
def test_actionlint_reports_violations(tmp_path: Path) -> None:
    """Assert that Lintro detects violations reported by actionlint.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    if not actionlint_available():
        pytest.skip("actionlint not available")
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    wf = wf_dir / "workflow_bad.yml"
    wf.write_text(SAMPLE_BAD.read_text())
    proc = subprocess.run(["actionlint", str(wf)], capture_output=True, text=True)  # nosec B603 B607 - fixed argv run against a real binary in a controlled test; binary name resolved from PATH, not attacker-controlled; shell=False, no user shell input
    direct_out = proc.stdout + proc.stderr
    logger.info(f"[LOG] actionlint stdout+stderr:\n{direct_out}")

    assert_that(proc.returncode).is_not_equal_to(0)
    tool = ToolRegistry.get("actionlint")
    assert_that(tool).is_not_none()
    result = tool.check([str(tmp_path)], {})
    logger.info(f"[LOG] lintro actionlint issues: {result.issues_count}")
    assert_that(result.issues_count > 0).is_true()
    assert_that(result.success).is_false()


@pytest.mark.actionlint
def test_actionlint_no_files(tmp_path: Path) -> None:
    """Assert that Lintro succeeds when no workflow files are present.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    if not actionlint_available():
        pytest.skip("actionlint not available")
    empty = tmp_path / "empty"
    empty.mkdir()
    tool = ToolRegistry.get("actionlint")
    assert_that(tool).is_not_none()
    result = tool.check([str(empty)], {})
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
