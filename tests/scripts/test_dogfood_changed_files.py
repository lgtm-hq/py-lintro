"""Tests for scripts/ci/dogfood-changed-files.sh.

Drives the changed-files dogfooding helper against real temporary git
repositories with a stubbed ``docker`` binary on PATH, asserting the diff
computation, the full-repo fallbacks, and the GITHUB_OUTPUT contract.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from pathlib import Path

import pytest
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT = (_REPO_ROOT / "scripts/ci/dogfood-changed-files.sh").resolve()

_DOCKER_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >>"${DOCKER_ARGS_LOG}"
if [[ "$1" == "run" ]]; then
    exit "${DOCKER_RUN_EXIT_CODE:-0}"
fi
if [[ "$1" == "pull" ]]; then
    exit "${DOCKER_PULL_EXIT_CODE:-0}"
fi
exit 0
"""


def _git(repo: Path, *args: str) -> None:
    """Run a git command inside a test repository.

    Args:
        repo: Repository working directory.
        *args: Git arguments (without the leading ``git``).
    """
    subprocess.run(  # nosec B603 B607 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
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
def pr_repo(tmp_path: Path) -> Path:
    """Create a git repo with a main branch and a feature branch diff.

    Args:
        tmp_path: Pytest-provided temporary directory.

    Returns:
        Path: The repository root, checked out on the feature branch with
        ``changed.py`` added and ``unchanged.py`` untouched vs main.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main")
    (repo / "unchanged.py").write_text("x = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    _git(repo, "checkout", "-b", "feature")
    (repo / "changed.py").write_text("y = 2\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "feature change")
    return repo


@pytest.fixture()
def docker_stub(tmp_path: Path) -> tuple[Path, Path]:
    """Install a docker stub that records its argv lines.

    Args:
        tmp_path: Pytest-provided temporary directory.

    Returns:
        tuple[Path, Path]: The stub bin directory (for PATH) and the file
        that collects one line per docker invocation.
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
    output_file: Path,
    *,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run dogfood-changed-files.sh in a test repo with the docker stub.

    Args:
        repo: Repository to run the script in.
        bin_dir: Directory containing the docker stub (prepended to PATH).
        args_log: File collecting docker stub invocations.
        output_file: File used as GITHUB_OUTPUT.
        extra_env: Additional environment overrides.

    Returns:
        subprocess.CompletedProcess[str]: The completed script run.
    """
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/local/bin",
        "HOME": os.environ.get("HOME", "/tmp"),  # nosec B108 - test env fallback only
        "LINTRO_IMAGE": "ghcr.io/lgtm-hq/py-lintro:ci-test",
        "BASE_REF": "main",
        "TOOL_OPTIONS": "pydoclint:timeout=120",
        "DOCKER_ARGS_LOG": str(args_log),
        "GITHUB_OUTPUT": str(output_file),
        "OUTPUT_LOG": str(repo / "chk-output.txt"),
        **(extra_env or {}),
    }
    return subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
        [str(_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        cwd=repo,
        env=env,
    )


def test_dogfood_changed_files_help() -> None:
    """dogfood-changed-files.sh should provide help and exit 0."""
    result = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
        [str(_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Usage:")


def test_requires_lintro_image_and_base_ref(tmp_path: Path) -> None:
    """Missing required environment variables should fail fast."""
    result = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
        [str(_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin"},
    )
    assert_that(result.returncode).is_not_equal_to(0)
    assert_that(result.stderr).contains("LINTRO_IMAGE")


def test_lints_only_changed_files(
    pr_repo: Path,
    docker_stub: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    """Changed-files mode passes only the diffed paths to lintro chk."""
    bin_dir, args_log = docker_stub
    output_file = tmp_path / "github-output"
    result = _run_script(pr_repo, bin_dir, args_log, output_file)

    assert_that(result.returncode).is_equal_to(0)
    docker_lines = args_log.read_text().splitlines()
    run_lines = [line for line in docker_lines if line.startswith("run ")]
    assert_that(run_lines).is_length(1)
    assert_that(run_lines[0]).contains("chk")
    assert_that(run_lines[0]).contains("changed.py")
    assert_that(run_lines[0]).contains("--tool-options pydoclint:timeout=120")
    assert_that(run_lines[0]).does_not_contain("unchanged.py")
    outputs = output_file.read_text()
    assert_that(outputs).contains("exit-code=0")
    assert_that(outputs).contains("status=passed")
    assert_that(outputs).contains("lint-mode=changed-files")


def test_empty_diff_passes_without_linting(
    pr_repo: Path,
    docker_stub: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    """A diff that leaves nothing lintable passes without running docker."""
    bin_dir, args_log = docker_stub
    # Reduce the branch to deletions only: remove the added file again.
    _git(pr_repo, "rm", "-q", "changed.py")
    _git(pr_repo, "commit", "-m", "drop feature file")
    output_file = tmp_path / "github-output"
    result = _run_script(pr_repo, bin_dir, args_log, output_file)

    assert_that(result.returncode).is_equal_to(0)
    assert_that(args_log.read_text()).is_empty()
    outputs = output_file.read_text()
    assert_that(outputs).contains("exit-code=0")
    assert_that(outputs).contains("status=passed")
    assert_that(outputs).contains("lint-mode=empty")


def test_unresolvable_base_falls_back_to_full_repo(
    pr_repo: Path,
    docker_stub: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    """An unresolvable base ref lints the whole repo instead of skipping."""
    bin_dir, args_log = docker_stub
    output_file = tmp_path / "github-output"
    result = _run_script(
        pr_repo,
        bin_dir,
        args_log,
        output_file,
        extra_env={"BASE_REF": "no-such-branch"},
    )

    assert_that(result.returncode).is_equal_to(0)
    run_lines = [
        line for line in args_log.read_text().splitlines() if line.startswith("run ")
    ]
    assert_that(run_lines).is_length(1)
    assert_that(run_lines[0]).contains(" . ")
    outputs = output_file.read_text()
    assert_that(outputs).contains("lint-mode=full-fallback")


def test_oversized_change_set_falls_back_to_full_repo(
    pr_repo: Path,
    docker_stub: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    """More changed files than MAX_CHANGED_FILES lints the whole repo."""
    bin_dir, args_log = docker_stub
    (pr_repo / "extra.py").write_text("z = 3\n")
    _git(pr_repo, "add", ".")
    _git(pr_repo, "commit", "-m", "second file")
    output_file = tmp_path / "github-output"
    result = _run_script(
        pr_repo,
        bin_dir,
        args_log,
        output_file,
        extra_env={"MAX_CHANGED_FILES": "1"},
    )

    assert_that(result.returncode).is_equal_to(0)
    run_lines = [
        line for line in args_log.read_text().splitlines() if line.startswith("run ")
    ]
    assert_that(run_lines).is_length(1)
    assert_that(run_lines[0]).contains(" . ")
    assert_that(run_lines[0]).does_not_contain("changed.py")
    outputs = output_file.read_text()
    assert_that(outputs).contains("lint-mode=full-fallback")


def test_pull_failure_still_writes_outputs(
    pr_repo: Path,
    docker_stub: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    """A failed image pull fails the script but keeps the output contract."""
    bin_dir, args_log = docker_stub
    output_file = tmp_path / "github-output"
    result = _run_script(
        pr_repo,
        bin_dir,
        args_log,
        output_file,
        extra_env={"DOCKER_PULL_EXIT_CODE": "3"},
    )

    assert_that(result.returncode).is_equal_to(3)
    run_lines = [
        line for line in args_log.read_text().splitlines() if line.startswith("run ")
    ]
    assert_that(run_lines).is_empty()
    outputs = output_file.read_text()
    assert_that(outputs).contains("exit-code=3")
    assert_that(outputs).contains("status=failed")


def test_lint_failure_propagates_exit_code(
    pr_repo: Path,
    docker_stub: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    """A failing lintro run fails the script and reports status=failed."""
    bin_dir, args_log = docker_stub
    output_file = tmp_path / "github-output"
    result = _run_script(
        pr_repo,
        bin_dir,
        args_log,
        output_file,
        extra_env={"DOCKER_RUN_EXIT_CODE": "5"},
    )

    assert_that(result.returncode).is_equal_to(5)
    outputs = output_file.read_text()
    assert_that(outputs).contains("exit-code=5")
    assert_that(outputs).contains("status=failed")
    assert_that(outputs).contains("lint-mode=changed-files")
