"""Tests for scripts/ci/ci-pr-comment.sh."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = _REPO_ROOT / "scripts" / "ci" / "ci-pr-comment.sh"


def test_ci_pr_comment_writes_unavailable_comment_without_report() -> None:
    """Script writes a fallback comment when no ``.lintro/run-*/report.md`` exists."""
    env = os.environ.copy()
    env.update(
        {
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_REPOSITORY": "test/repo",
            "GITHUB_RUN_ID": "12345",
        },
    )

    required_commands = (
        "bash",
        "cat",
        "dirname",
        "find",
        "head",
        "ls",
        "mktemp",
        "python3",
        "rm",
        "xargs",
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        bin_dir = Path(tmpdir) / "bin"
        bin_dir.mkdir()
        for command in required_commands:
            resolved = shutil.which(command)
            if resolved is None:
                raise RuntimeError(f"Required command not found: {command}")
            (bin_dir / command).symlink_to(resolved)

        env["PATH"] = str(bin_dir)

        result = subprocess.run(
            [str(SCRIPT_PATH)],
            capture_output=True,
            text=True,
            cwd=tmpdir,
            env=env,
            check=False,
        )

        assert_that(result.returncode).is_equal_to(0)

        comment_file = Path(tmpdir) / "pr-comment.txt"
        assert_that(comment_file.exists()).is_true()

        content = comment_file.read_text(encoding="utf-8")
        assert_that(content).contains("⚠️ OUTPUT UNAVAILABLE")
        assert_that(content).contains("`report.md` was unavailable")
        assert_that(content).contains(
            "lintro run artifact (.lintro/run-*/report.md)",
        )


def test_ci_pr_comment_builds_from_report_md(tmp_path: Path) -> None:
    """Script builds a comment from a newest-wins ``report.md``."""
    env = os.environ.copy()
    env.update(
        {
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_REPOSITORY": "test/repo",
            "GITHUB_RUN_ID": "12345",
            "CHK_EXIT_CODE": "0",
        },
    )

    required_commands = (
        "bash",
        "cat",
        "dirname",
        "find",
        "head",
        "ls",
        "mktemp",
        "python3",
        "rm",
        "xargs",
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for command in required_commands:
        resolved = shutil.which(command)
        if resolved is None:
            raise RuntimeError(f"Required command not found: {command}")
        (bin_dir / command).symlink_to(resolved)
    env["PATH"] = str(bin_dir)

    run_dir = tmp_path / ".lintro" / "run-20260417T000000"
    run_dir.mkdir(parents=True)
    (run_dir / "report.md").write_text(
        "# Lintro Report\n\n_Generated 2026-04-17 · run-test_\n\n"
        "```text\nEXECUTION SUMMARY\n✅ all good\n```\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [str(SCRIPT_PATH)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        check=False,
    )

    assert_that(result.returncode).is_equal_to(0)
    content = (tmp_path / "pr-comment.txt").read_text(encoding="utf-8")
    assert_that(content).contains("✅ PASSED")
    assert_that(content).contains("EXECUTION SUMMARY")
