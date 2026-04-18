"""Tests for `scripts/ci/tools-image-detect-changes.sh`."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
from pathlib import Path

from assertpy import assert_that

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "ci" / "tools-image-detect-changes.sh"


def _write_fake_git(bin_dir: Path) -> None:
    """Write a fake `git` executable that returns mock changed files.

    Args:
        bin_dir: Directory that will contain the fake `git` binary.
    """
    fake_git = bin_dir / "git"
    fake_git.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "${1:-}" == "diff" && "${2:-}" == "--name-only" ]]; then\n'
        "  printf '%s\\n' \"${MOCK_CHANGED_FILES:-}\"\n"
        "  exit 0\n"
        "fi\n"
        'echo "unexpected git invocation: $*" >&2\n'
        "exit 1\n",
    )
    fake_git.chmod(fake_git.stat().st_mode | stat.S_IXUSR)


def _run_script(
    *,
    changed_files: str,
    event: str = "pull_request",
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run the detect-changes script with a fake `git diff` response.

    Args:
        changed_files: Newline-delimited file list returned by fake `git diff`.
        event: GitHub event name passed to the script.

    Returns:
        Tuple of subprocess result and output file contents.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_dir = Path(tmpdir)
        bin_dir = temp_dir / "bin"
        bin_dir.mkdir()
        _write_fake_git(bin_dir=bin_dir)

        output_file = temp_dir / "github-output.txt"
        env = os.environ.copy()
        env.update(
            {
                "GITHUB_EVENT_NAME": event,
                "GITHUB_OUTPUT": str(output_file),
                "MOCK_CHANGED_FILES": changed_files,
                "PATH": f"{bin_dir}:{env['PATH']}",
            },
        )

        if event == "pull_request":
            env.update(
                {
                    "PR_BASE_SHA": "base-sha",
                    "PR_HEAD_SHA": "head-sha",
                },
            )
        elif event == "push":
            env["GITHUB_EVENT_BEFORE"] = "before-sha"

        result = subprocess.run(
            [str(SCRIPT_PATH)],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=env,
            check=False,
        )

        output_text = output_file.read_text() if output_file.exists() else ""
        return result, output_text


def test_detect_changes_matches_tools_image_script_glob() -> None:
    """Script changes under `scripts/ci/tools-image-*.sh` trigger fresh image usage."""
    result, output_text = _run_script(
        changed_files="scripts/ci/tools-image-resolve.sh\nREADME.md",
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(output_text).contains("tools_changed=true")
    assert_that(result.stdout).contains("scripts/ci/tools-image-*.sh")


def test_detect_changes_matches_tools_image_on_push() -> None:
    """Push events use the same tools-image glob detection as pull requests."""
    result, output_text = _run_script(
        changed_files="scripts/ci/tools-image-resolve.sh\nREADME.md",
        event="push",
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(output_text).contains("tools_changed=true")
    assert_that(result.stdout).contains("scripts/ci/tools-image-*.sh")


def test_detect_changes_ignores_unrelated_files() -> None:
    """Unrelated file changes keep the stable image path."""
    result, output_text = _run_script(
        changed_files="README.md\ndocs/usage.md",
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(output_text).contains("tools_changed=false")
    assert_that(result.stdout).contains("No tool file changes detected")
