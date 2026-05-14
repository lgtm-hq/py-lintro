"""Tests for `scripts/ci/tools-image-resolve.sh`."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
from pathlib import Path

from assertpy import assert_that

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "ci" / "tools-image-resolve.sh"
IMAGE_NAME = "ghcr.io/example/lintro-tools"
STABLE_IMAGE = f"{IMAGE_NAME}:latest@sha256:{'a' * 64}"
MERGE_GROUP_SHA = "1234567890abcdef1234567890abcdef12345678"


def _write_fake_docker(bin_dir: Path) -> None:
    """Write a fake docker executable that fails if the resolver shells out to it.

    Args:
        bin_dir: Directory that will contain the fake `docker` binary.
    """
    fake_docker = bin_dir / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "echo 'docker should not be called by this test' >&2\n"
        "exit 42\n",
    )
    fake_docker.chmod(fake_docker.stat().st_mode | stat.S_IXUSR)


def _run_resolve(
    *,
    event: str,
    tools_changed: str = "false",
    call_result: str = "",
    pr_number: str = "",
    github_sha: str = MERGE_GROUP_SHA,
    stable_image: str = STABLE_IMAGE,
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run the tools image resolver with an isolated GitHub output file.

    Args:
        event: GitHub event name passed to the resolver.
        tools_changed: Value for TOOLS_CHANGED.
        call_result: Value for CALL_RESULT.
        pr_number: Value for PR_NUMBER.
        github_sha: Value for GITHUB_SHA.
        stable_image: Value for STABLE_IMAGE.

    Returns:
        Tuple of subprocess result and output file contents.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_dir = Path(tmpdir)
        bin_dir = temp_dir / "bin"
        bin_dir.mkdir()
        _write_fake_docker(bin_dir=bin_dir)

        output_file = temp_dir / "github-output.txt"
        env = os.environ.copy()
        env.update(
            {
                "CALL_RESULT": call_result,
                "GITHUB_EVENT_NAME": event,
                "GITHUB_OUTPUT": str(output_file),
                "GITHUB_SHA": github_sha,
                "IMAGE_NAME": IMAGE_NAME,
                "PATH": f"{bin_dir}:{env['PATH']}",
                "PR_NUMBER": pr_number,
                "STABLE_IMAGE": stable_image,
                "TOOLS_CHANGED": tools_changed,
            },
        )

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


def test_pr_workflow_call_success_resolves_pr_image() -> None:
    """PRs with successful fresh tools-image builds use the PR image tag."""
    result, output_text = _run_resolve(
        event="pull_request",
        tools_changed="true",
        call_result="success",
        pr_number="123",
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(output_text).contains(f"image={IMAGE_NAME}:pr-123")
    assert_that(output_text).contains("source=registry")
    assert_that(result.stdout).contains("Using pre-built tools image")


def test_merge_group_workflow_call_success_resolves_sha_image() -> None:
    """Merge queue fresh tools-image builds use the merge-group commit tag."""
    result, output_text = _run_resolve(
        event="merge_group",
        tools_changed="true",
        call_result="success",
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(output_text).contains(f"image={IMAGE_NAME}:sha-{MERGE_GROUP_SHA}")
    assert_that(output_text).contains("source=registry")
    assert_that(result.stdout).contains("Using pre-built tools image")


def test_main_push_with_tool_changes_uses_stable_image_without_docker_polling() -> None:
    """Main push CI uses the stable image even when tool files changed."""
    result, output_text = _run_resolve(
        event="push",
        tools_changed="true",
        call_result="skipped",
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(output_text).contains(f"image={STABLE_IMAGE}")
    assert_that(output_text).contains("source=stable")
    assert_that(result.stdout).contains("Build - Tools Image validates")
    assert_that(result.stderr).does_not_contain("docker should not be called")


def test_main_push_without_tool_changes_uses_stable_image() -> None:
    """Main push CI keeps using the pinned stable image when tools are unchanged."""
    result, output_text = _run_resolve(
        event="push",
        tools_changed="false",
        call_result="skipped",
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(output_text).contains(f"image={STABLE_IMAGE}")
    assert_that(output_text).contains("source=stable")
    assert_that(result.stdout).contains("Using stable tools image")
