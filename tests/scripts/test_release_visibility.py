"""Tests for release automation visibility helper.

The helper keeps workflow-run release failures discoverable by writing trigger
context to the step summary and creating/updating GitHub issues on failure.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from assertpy import assert_that

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "github" / "release-visibility.sh"


def _base_env(tmp_path: Path) -> dict[str, str]:
    """Build a minimal GitHub Actions environment for helper tests.

    Args:
        tmp_path: Temporary directory for summary and mock files.

    Returns:
        Environment variables for invoking the helper.
    """
    return {
        "GITHUB_ACTOR": "TurboCoder13",
        "GITHUB_EVENT_NAME": "workflow_run",
        "GITHUB_REF_NAME": "main",
        "GITHUB_REPOSITORY": "lgtm-hq/py-lintro",
        "GITHUB_RUN_ID": "25905377365",
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_SHA": "d01c63521e5da733888f154639ee09fb1e583f19",
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        "GITHUB_WORKFLOW": "Release - Automated PR Creation",
        "UPSTREAM_CONCLUSION": "success",
        "UPSTREAM_HEAD_BRANCH": "main",
        "UPSTREAM_HEAD_SHA": "d01c63521e5da733888f154639ee09fb1e583f19",
        "UPSTREAM_RUN_ID": "25905005234",
        "UPSTREAM_RUN_URL": "https://github.com/lgtm-hq/py-lintro/actions/runs/25905005234",
        "UPSTREAM_WORKFLOW_NAME": "Build - Tools Image",
    }


def _run_helper(
    command: str,
    *,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    """Run the release visibility helper.

    Args:
        command: Helper subcommand to execute.
        env: Environment variables for the subprocess.

    Returns:
        Completed helper subprocess.
    """
    return subprocess.run(
        ["bash", str(SCRIPT), command],
        capture_output=True,
        check=False,
        env=env,
        text=True,
    )


def _install_mock_gh(
    tmp_path: Path,
    *,
    existing_issue: str = "",
) -> dict[str, str]:
    """Install a mock gh executable that records commands.

    Args:
        tmp_path: Temporary directory for the mock executable and logs.
        existing_issue: Issue number returned by `gh issue list`.

    Returns:
        Environment variables needed by the mock.
    """
    mock_dir = tmp_path / "mock-bin"
    mock_dir.mkdir()
    log_file = tmp_path / "gh.log"
    body_file = tmp_path / "body.md"
    gh_script = mock_dir / "gh"
    gh_script.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$GH_MOCK_LOG"
if [[ "$1 $2" == "issue list" ]]; then
  printf '%s\\n' "${GH_EXISTING_ISSUE:-}"
  exit 0
fi
if [[ "$1 $2" == "run view" ]]; then
  printf '%s\\n' "- **Job:** Compute next version and open Release PR"
  printf '%s\\n' "  **Step:** Run Lintro check"
  exit 0
fi
for ((idx = 1; idx <= $#; idx++)); do
  current="${!idx}"
  if [[ "$current" == "--body-file" ]]; then
    next=$((idx + 1))
    cp "${!next}" "$GH_BODY_CAPTURE"
  fi
done
exit 0
""",
    )
    gh_script.chmod(0o755)
    return {
        "GH_BODY_CAPTURE": str(body_file),
        "GH_EXISTING_ISSUE": existing_issue,
        "GH_MOCK_LOG": str(log_file),
        "PATH": f"{mock_dir}{os.pathsep}{os.environ['PATH']}",
    }


def test_write_summary_includes_workflow_run_origin(tmp_path: Path) -> None:
    """Workflow-run summaries include upstream workflow context."""
    env = os.environ.copy()
    env.update(_base_env(tmp_path=tmp_path))

    result = _run_helper(command="write_summary", env=env)

    assert_that(result.returncode).described_as(result.stderr).is_equal_to(0)
    summary = (tmp_path / "summary.md").read_text()
    assert_that(summary).contains("## Release Automation Context")
    assert_that(summary).contains("**Event:** workflow_run")
    assert_that(summary).contains("**Workflow:** Build - Tools Image")
    assert_that(summary).contains("**Run ID:** 25905005234")
    assert_that(summary).contains(
        "**Head SHA:** d01c63521e5da733888f154639ee09fb1e583f19",
    )


def test_write_summary_for_push_does_not_require_upstream_fields(
    tmp_path: Path,
) -> None:
    """Push summaries render without workflow_run-only fields."""
    env = os.environ.copy()
    env.update(_base_env(tmp_path=tmp_path))
    env["GITHUB_EVENT_NAME"] = "push"
    for key in list(env):
        if key.startswith("UPSTREAM_"):
            env.pop(key)

    result = _run_helper(command="write_summary", env=env)

    assert_that(result.returncode).described_as(result.stderr).is_equal_to(0)
    summary = (tmp_path / "summary.md").read_text()
    assert_that(summary).contains("**Event:** push")
    assert_that(summary).does_not_contain("### Upstream Workflow")


def test_notify_failure_creates_issue_when_none_exists(tmp_path: Path) -> None:
    """Main release failures create a labeled issue when no match exists."""
    env = os.environ.copy()
    env.update(_base_env(tmp_path=tmp_path))
    env.update(_install_mock_gh(tmp_path=tmp_path))

    result = _run_helper(command="notify_failure", env=env)

    assert_that(result.returncode).described_as(result.stderr).is_equal_to(0)
    gh_log = (tmp_path / "gh.log").read_text()
    body = (tmp_path / "body.md").read_text()
    assert_that(gh_log).contains("issue list")
    assert_that(gh_log).contains(
        "release-automation-failure:Release - Automated PR Creation:main",
    )
    assert_that(gh_log).contains("issue create")
    assert_that(gh_log).does_not_contain("issue comment")
    assert_that(body).contains("release-automation-failure")
    assert_that(body).contains("Run Lintro check")
    assert_that(body).contains("25905377365")


def test_notify_failure_comments_on_existing_issue(tmp_path: Path) -> None:
    """Repeated main failures comment on an existing release failure issue."""
    env = os.environ.copy()
    env.update(_base_env(tmp_path=tmp_path))
    env.update(_install_mock_gh(tmp_path=tmp_path, existing_issue="907"))

    result = _run_helper(command="notify_failure", env=env)

    assert_that(result.returncode).described_as(result.stderr).is_equal_to(0)
    gh_log = (tmp_path / "gh.log").read_text()
    assert_that(gh_log).contains("issue list")
    assert_that(gh_log).contains(
        "release-automation-failure:Release - Automated PR Creation:main",
    )
    assert_that(gh_log).contains("issue comment 907")
    assert_that(gh_log).does_not_contain("issue create")


def test_notify_failure_skips_non_main_branch(tmp_path: Path) -> None:
    """Non-main release failures do not create operational issues."""
    env = os.environ.copy()
    env.update(_base_env(tmp_path=tmp_path))
    env["UPSTREAM_HEAD_BRANCH"] = "feature/test"
    env["PATH"] = "/usr/bin:/bin"

    result = _run_helper(command="notify_failure", env=env)

    assert_that(result.returncode).described_as(result.stderr).is_equal_to(0)
    assert_that(result.stdout).contains("skipped for branch 'feature/test'")


def test_release_visibility_script_has_valid_bash_syntax() -> None:
    """The release visibility helper parses as valid bash."""
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True,
        check=False,
        text=True,
    )

    assert_that(result.returncode).described_as(result.stderr).is_equal_to(0)
