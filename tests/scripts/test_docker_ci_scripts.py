"""Tests for docker-ci workflow helper shell scripts."""

from __future__ import annotations

import os
import subprocess
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
        "scripts/ci/maintenance/sweep-ci-ghcr-tags.sh",
        "scripts/ci/verify-tools.sh",
        "scripts/docker/save-ci-images-tarball.sh",
        "scripts/docker/run-docker-test-suite.sh",
        "scripts/docker/smoke-test-base-image.sh",
    ],
)
def test_docker_ci_scripts_expose_help(script: str) -> None:
    """Each docker-ci helper script should support --help."""
    script_path = (_REPO_ROOT / script).resolve()
    result = subprocess.run(
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
        result = subprocess.run(
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
    result = subprocess.run(
        [str(script_path), "full"],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ.copy(), "CI_TAG": ""},
    )
    assert_that(result.returncode).is_equal_to(2)
    assert_that(result.stderr).contains("CI_TAG is required")


def test_sweep_ci_ghcr_tags_requires_token() -> None:
    """sweep-ci-ghcr-tags.sh should fail when GH_TOKEN is missing."""
    script_path = (
        _REPO_ROOT / "scripts/ci/maintenance/sweep-ci-ghcr-tags.sh"
    ).resolve()
    result = subprocess.run(
        [str(script_path)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ.copy(), "GH_TOKEN": ""},
    )
    assert_that(result.returncode).is_equal_to(2)
    assert_that(result.stderr).contains("GH_TOKEN is required")


def test_sweep_ci_ghcr_tags_validates_min_age_days() -> None:
    """sweep-ci-ghcr-tags.sh should reject a non-integer MIN_AGE_DAYS."""
    script_path = (
        _REPO_ROOT / "scripts/ci/maintenance/sweep-ci-ghcr-tags.sh"
    ).resolve()
    result = subprocess.run(
        [str(script_path)],
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ.copy(),
            "GH_TOKEN": "x",  # noqa: S106 - placeholder token for arg validation
            "MIN_AGE_DAYS": "notanumber",
        },
    )
    assert_that(result.returncode).is_equal_to(2)
    assert_that(result.stderr).contains("MIN_AGE_DAYS must be")


def test_verify_tools_rejects_unknown_argument() -> None:
    """verify-tools.sh should reject unknown flags with a usage error."""
    script_path = (_REPO_ROOT / "scripts/ci/verify-tools.sh").resolve()
    result = subprocess.run(
        [str(script_path), "--nope"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert_that(result.returncode).is_equal_to(2)
    assert_that(result.stderr).contains("Unknown argument")


def test_verify_tools_label_requires_value() -> None:
    """verify-tools.sh --label should require a value."""
    script_path = (_REPO_ROOT / "scripts/ci/verify-tools.sh").resolve()
    result = subprocess.run(
        [str(script_path), "--label"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert_that(result.returncode).is_equal_to(2)
    assert_that(result.stderr).contains("--label requires a value")
