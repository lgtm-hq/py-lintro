"""Tests for docker-ci workflow helper shell scripts."""

from __future__ import annotations

import os
import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
import tempfile
from pathlib import Path

import pytest
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.mark.parametrize(
    "script",
    [
        "scripts/ci/detect-fork-pr.sh",
        "scripts/ci/free-disk-space.sh",
        "scripts/ci/fail-on-security-audit.sh",
        "scripts/ci/testing/pull-ci-docker-images.sh",
        "scripts/ci/testing/load-ci-docker-images.sh",
        "scripts/ci/maintenance/delete-ci-ghcr-tags.sh",
        "scripts/docker/save-ci-images-tarball.sh",
        "scripts/docker/run-docker-test-suite.sh",
        "scripts/docker/smoke-test-base-image.sh",
    ],
)
def test_docker_ci_scripts_expose_help(script: str) -> None:
    """Each docker-ci helper script should support --help."""
    script_path = (_REPO_ROOT / script).resolve()
    result = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
        [str(script_path), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Usage:")


@pytest.mark.parametrize(
    ("event_name", "is_fork_pr", "expected"),
    [
        ("pull_request", "true", "is-fork=true"),
        ("pull_request", "false", "is-fork=false"),
        ("push", "false", "is-fork=false"),
    ],
)
def test_detect_fork_pr_writes_github_output(
    event_name: str,
    is_fork_pr: str,
    expected: str,
) -> None:
    """detect-fork-pr.sh should write is-fork to GITHUB_OUTPUT."""
    script_path = (_REPO_ROOT / "scripts/ci/detect-fork-pr.sh").resolve()
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as output_file:
        output_path = output_file.name

    try:
        result = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
            [str(script_path)],
            capture_output=True,
            text=True,
            check=False,
            env={
                **os.environ.copy(),
                "EVENT_NAME": event_name,
                "IS_FORK_PR": is_fork_pr,
                "GITHUB_OUTPUT": output_path,
            },
        )

        assert_that(result.returncode).is_equal_to(0)
        assert_that(Path(output_path).read_text().strip()).is_equal_to(expected)
    finally:
        Path(output_path).unlink(missing_ok=True)


def test_pull_ci_docker_images_requires_ci_tag() -> None:
    """pull-ci-docker-images.sh should fail when CI_TAG is missing."""
    script_path = (_REPO_ROOT / "scripts/ci/testing/pull-ci-docker-images.sh").resolve()
    result = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
        [str(script_path), "full"],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ.copy(), "CI_TAG": ""},
    )
    assert_that(result.returncode).is_equal_to(2)
    assert_that(result.stderr).contains("CI_TAG is required")
