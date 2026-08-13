"""Tests for ``scripts/ci/install-cursor-agent.sh``.

The dogfood job installs the pinned Cursor ``agent`` CLI onto a bare runner.
These tests cover the script's local validation — missing pins, malformed
build ids, malformed digests — so a bad caller fails before it hits the
network. The download itself is exercised in CI, not here.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - subprocess is used to drive the script under test; invocations use shell=False
from pathlib import Path

from assertpy import assert_that

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "install-cursor-agent.sh"

VALID_VERSION = "2026.07.23-e383d2b"
VALID_SHA256 = "a" * 64


def _run(
    *,
    env_overrides: dict[str, str],
    args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the installer with a controlled environment.

    Args:
        env_overrides: Environment variables layered onto a minimal base.
        args: Optional positional arguments passed to the script.

    Returns:
        The completed subprocess result.
    """
    env = {"PATH": os.environ.get("PATH", "")}
    env.update(env_overrides)
    return subprocess.run(  # nosec B603 - fixed argv run against a real binary; shell=False
        [str(SCRIPT), *(args or [])],
        capture_output=True,
        text=True,
        env=env,
    )


def test_help_exits_zero() -> None:
    """The --help flag prints usage and exits 0."""
    result = _run(
        env_overrides={},
        args=["--help"],
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Usage:")
    assert_that(result.stdout).contains("CURSOR_AGENT_VERSION")


def test_missing_version_fails() -> None:
    """A missing version pin fails before any download."""
    result = _run(env_overrides={"CURSOR_AGENT_SHA256_X64": VALID_SHA256})

    assert_that(result.returncode).is_not_equal_to(0)
    assert_that(result.stderr).contains("CURSOR_AGENT_VERSION is required")


def test_non_calendar_version_fails() -> None:
    """``latest`` and path fragments are refused so the URL cannot move."""
    result = _run(
        env_overrides={
            "CURSOR_AGENT_VERSION": "latest",
            "CURSOR_AGENT_SHA256_X64": VALID_SHA256,
        },
    )

    assert_that(result.returncode).is_not_equal_to(0)
    assert_that(result.stderr).contains("calendar build id")


def test_path_injection_in_version_fails() -> None:
    """A version with slashes must not be interpolated into the download URL."""
    result = _run(
        env_overrides={
            "CURSOR_AGENT_VERSION": "../evil",
            "CURSOR_AGENT_SHA256_X64": VALID_SHA256,
        },
    )

    assert_that(result.returncode).is_not_equal_to(0)
    assert_that(result.stderr).contains("calendar build id")


def test_malformed_digest_fails() -> None:
    """A non-sha256 pin fails before curl, so a typo cannot skip verification."""
    result = _run(
        env_overrides={
            "CURSOR_AGENT_VERSION": VALID_VERSION,
            "CURSOR_AGENT_SHA256_X64": "not-a-digest",
            "CURSOR_AGENT_SHA256_ARM64": "not-a-digest",
        },
    )

    assert_that(result.returncode).is_not_equal_to(0)
    assert_that(result.stderr).contains("64-character hex digest")
