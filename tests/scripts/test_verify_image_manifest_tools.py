"""Tests for scripts/ci/verify-image-manifest-tools.sh.

Drives the image-verification wrapper against a stubbed ``docker`` binary in a
temporary git repository, asserting the docker invocation contract (entrypoint
override, read-only checkout mount, manifest path, tier selection) and the
usage/error handling.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - drives the shell script under test with shell=False
from pathlib import Path

import pytest
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = (_REPO_ROOT / "scripts/ci/verify-image-manifest-tools.sh").resolve()

_DOCKER_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >>"${DOCKER_ARGS_LOG}"
exit "${DOCKER_RUN_EXIT_CODE:-0}"
"""


def _git(repo: Path, *args: str) -> None:
    """Run a git command inside a test repository.

    Args:
        repo: Repository working directory.
        *args: Git arguments (without the leading ``git``).
    """
    subprocess.run(  # nosec B603 B607 - fixed argv against a real binary in a controlled test; shell=False
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        },
    )


@pytest.fixture()
def image_repo(tmp_path: Path) -> Path:
    """Create a git repo containing a manifest at the expected path.

    Args:
        tmp_path: Pytest-provided temporary directory.

    Returns:
        Path: The repository root with ``lintro/tools/manifest.json`` present.
    """
    repo = tmp_path / "repo"
    (repo / "lintro" / "tools").mkdir(parents=True)
    (repo / "lintro" / "tools" / "manifest.json").write_text(
        '{"version": 2, "tools": []}\n',
    )
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


@pytest.fixture()
def docker_stub(tmp_path: Path) -> tuple[Path, Path]:
    """Install a docker stub that records its argv lines.

    Args:
        tmp_path: Pytest-provided temporary directory.

    Returns:
        tuple[Path, Path]: The stub bin directory (for PATH) and the file that
        collects one line per docker invocation.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    args_log = tmp_path / "docker-args.log"
    args_log.touch()
    stub = bin_dir / "docker"
    stub.write_text(_DOCKER_STUB)
    stub.chmod(0o755)
    return bin_dir, args_log


def _run_script(
    repo: Path,
    bin_dir: Path,
    args_log: Path,
    *,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run verify-image-manifest-tools.sh in a test repo with the docker stub.

    Args:
        repo: Repository to run the script in.
        bin_dir: Directory containing the docker stub (prepended to PATH).
        args_log: File collecting docker stub invocations.
        extra_env: Additional environment overrides.

    Returns:
        subprocess.CompletedProcess[str]: The completed script run.
    """
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/local/bin",
        "HOME": os.environ.get("HOME", "/tmp"),  # nosec B108 - test env fallback only
        "IMAGE": "ghcr.io/lgtm-hq/py-lintro:ci-test",
        "DOCKER_ARGS_LOG": str(args_log),
        **(extra_env or {}),
    }
    return subprocess.run(  # nosec B603 - fixed argv against a real binary in a controlled test; shell=False
        [str(_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        cwd=repo,
        env=env,
    )


def test_help_exits_zero() -> None:
    """The wrapper should print help and exit 0."""
    result = subprocess.run(  # nosec B603 - fixed argv against a real binary in a controlled test; shell=False
        [str(_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Usage:")
    assert_that(result.stdout).contains("IMAGE")


def test_requires_image(tmp_path: Path) -> None:
    """A missing IMAGE should fail fast with a diagnostic."""
    result = subprocess.run(  # nosec B603 - fixed argv against a real binary in a controlled test; shell=False
        [str(_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin"},
    )
    assert_that(result.returncode).is_not_equal_to(0)
    assert_that(result.stderr).contains("IMAGE")


def test_missing_manifest_exits_two(
    tmp_path: Path,
    docker_stub: tuple[Path, Path],
) -> None:
    """A manifest that does not exist should exit 2 without running docker."""
    bin_dir, args_log = docker_stub
    # A git repo with no manifest: repo-root resolution lands here (not the
    # real checkout), so the manifest-existence guard fails as intended.
    repo = tmp_path / "empty-repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main")
    result = _run_script(repo, bin_dir, args_log)
    assert_that(result.returncode).is_equal_to(2)
    assert_that(result.stderr).contains("Manifest not found")
    assert_that(args_log.read_text()).is_empty()


def test_runs_verifier_inside_image(
    image_repo: Path,
    docker_stub: tuple[Path, Path],
) -> None:
    """The wrapper runs the verifier inside the image with the expected argv."""
    bin_dir, args_log = docker_stub
    result = _run_script(image_repo, bin_dir, args_log)

    assert_that(result.returncode).is_equal_to(0)
    run_lines = [
        line for line in args_log.read_text().splitlines() if line.startswith("run ")
    ]
    assert_that(run_lines).is_length(1)
    invocation = run_lines[0]
    # Entrypoint bypassed so the container's baked ENV/PATH is used verbatim.
    assert_that(invocation).contains("--entrypoint python3")
    # Checkout mounted read-only outside the entrypoint's /code gosu path.
    assert_that(invocation).contains(f"-v {image_repo}:/repo:ro")
    assert_that(invocation).contains("-w /repo")
    assert_that(invocation).contains("ghcr.io/lgtm-hq/py-lintro:ci-test")
    assert_that(invocation).contains("scripts/ci/verify-manifest-tools.py")
    assert_that(invocation).contains("--manifest lintro/tools/manifest.json")
    # Default tier selection.
    assert_that(invocation).contains("--tiers tools")


def test_tiers_override_passed_through(
    image_repo: Path,
    docker_stub: tuple[Path, Path],
) -> None:
    """A custom TIERS value flows through to the verifier invocation."""
    bin_dir, args_log = docker_stub
    result = _run_script(
        image_repo,
        bin_dir,
        args_log,
        extra_env={"TIERS": "tools,dev"},
    )
    assert_that(result.returncode).is_equal_to(0)
    invocation = args_log.read_text().splitlines()[0]
    assert_that(invocation).contains("--tiers tools,dev")


def test_dry_run_skips_docker(
    image_repo: Path,
    docker_stub: tuple[Path, Path],
) -> None:
    """DRY_RUN prints the command and never invokes docker."""
    bin_dir, args_log = docker_stub
    result = _run_script(
        image_repo,
        bin_dir,
        args_log,
        extra_env={"DRY_RUN": "1"},
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("[DRY-RUN]")
    assert_that(args_log.read_text()).is_empty()


def test_drift_failure_propagates_exit_code(
    image_repo: Path,
    docker_stub: tuple[Path, Path],
) -> None:
    """A non-zero verifier exit (manifest-vs-image drift) fails the wrapper."""
    bin_dir, args_log = docker_stub
    result = _run_script(
        image_repo,
        bin_dir,
        args_log,
        extra_env={"DOCKER_RUN_EXIT_CODE": "1"},
    )
    assert_that(result.returncode).is_equal_to(1)
