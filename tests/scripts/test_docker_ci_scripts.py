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
        "scripts/ci/promote-ci-docker-images.sh",
        "scripts/ci/cosign-sign-images.sh",
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


def _write_stub(bin_dir: Path, name: str, body: str) -> None:
    """Write an executable stub binary into a PATH shim directory.

    Args:
        bin_dir: Directory prepended to PATH for the script under test.
        name: Binary name to shadow (e.g. ``docker``).
        body: Bash script body executed when the stub is invoked.
    """
    stub = bin_dir / name
    stub.write_text(f"#!/usr/bin/env bash\n{body}\n")
    stub.chmod(0o755)


def _run_with_stubs(
    script: str,
    bin_dir: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    """Run a repo script with stub binaries shadowing PATH lookups.

    Args:
        script: Script path relative to the repository root.
        bin_dir: Directory containing stub binaries, prepended to PATH.
        env: Extra environment variables for the script.

    Returns:
        subprocess.CompletedProcess[str]: The completed process.
    """
    script_path = (_REPO_ROOT / script).resolve()
    return subprocess.run(  # nosec B603 - fixed argv run against a repo script in a controlled test; shell=False, no user shell input
        [str(script_path)],
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ.copy(),
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            **env,
        },
    )


@pytest.mark.parametrize(
    "missing",
    ["SOURCE_IMAGE", "CI_TAG", "TAGS"],
)
def test_promote_ci_docker_images_requires_env(missing: str) -> None:
    """promote-ci-docker-images.sh should fail fast on missing env vars."""
    script_path = (_REPO_ROOT / "scripts/ci/promote-ci-docker-images.sh").resolve()
    env = {
        "SOURCE_IMAGE": "ghcr.io/example/app",
        "CI_TAG": "ci-1",
        "TAGS": "ghcr.io/example/app:main",
    }
    env[missing] = ""
    result = subprocess.run(  # nosec B603 - fixed argv run against a repo script in a controlled test; shell=False, no user shell input
        [str(script_path)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ.copy(), **env},
    )
    assert_that(result.returncode).is_equal_to(2)
    assert_that(result.stderr).contains(f"{missing} is required")


def test_promote_ci_docker_images_promotes_by_digest(tmp_path: Path) -> None:
    """The promote script should retag by digest and verify each tag."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker_log = tmp_path / "docker.log"
    github_output = tmp_path / "github_output"
    github_output.touch()
    _write_stub(
        bin_dir,
        "docker",
        (
            'echo "$*" >> "$DOCKER_LOG"\n'
            'if [[ "$*" == *" inspect "* ]]; then\n'
            '  echo "sha256:aaa111"\n'
            "fi"
        ),
    )

    result = _run_with_stubs(
        "scripts/ci/promote-ci-docker-images.sh",
        bin_dir,
        {
            "SOURCE_IMAGE": "ghcr.io/example/app",
            "CI_TAG": "ci-1",
            "TAGS": "ghcr.io/example/app:main\nghcr.io/example/app:sha-abc",
            "DOCKER_LOG": str(docker_log),
            "GITHUB_OUTPUT": str(github_output),
        },
    )

    assert_that(result.returncode).is_equal_to(0)
    log = docker_log.read_text()
    assert_that(log).contains(
        "buildx imagetools create ghcr.io/example/app@sha256:aaa111 "
        "--tag ghcr.io/example/app:main --tag ghcr.io/example/app:sha-abc",
    )
    # Source resolve + two per-tag verifications.
    assert_that(log).contains("{{.Manifest.Digest}} ghcr.io/example/app:ci-1")
    assert_that(log).contains("{{.Manifest.Digest}} ghcr.io/example/app:main")
    assert_that(log).contains("{{.Manifest.Digest}} ghcr.io/example/app:sha-abc")
    assert_that(github_output.read_text()).contains("digest=sha256:aaa111")


def test_promote_ci_docker_images_fails_on_digest_mismatch(
    tmp_path: Path,
) -> None:
    """A promoted tag resolving to a different digest should fail the run."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stub(
        bin_dir,
        "docker",
        (
            'if [[ "$*" == *" inspect "* ]]; then\n'
            '  if [[ "${@: -1}" == "ghcr.io/example/app:ci-1" ]]; then\n'
            '    echo "sha256:aaa111"\n'
            "  else\n"
            '    echo "sha256:bbb222"\n'
            "  fi\n"
            "fi"
        ),
    )

    result = _run_with_stubs(
        "scripts/ci/promote-ci-docker-images.sh",
        bin_dir,
        {
            "SOURCE_IMAGE": "ghcr.io/example/app",
            "CI_TAG": "ci-1",
            "TAGS": "ghcr.io/example/app:main",
        },
    )

    assert_that(result.returncode).is_equal_to(1)
    assert_that(result.stderr).contains("Digest mismatch after promotion")


def test_cosign_sign_images_requires_images() -> None:
    """cosign-sign-images.sh should fail when IMAGES is missing."""
    script_path = (_REPO_ROOT / "scripts/ci/cosign-sign-images.sh").resolve()
    result = subprocess.run(  # nosec B603 - fixed argv run against a repo script in a controlled test; shell=False, no user shell input
        [str(script_path)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ.copy(), "IMAGES": ""},
    )
    assert_that(result.returncode).is_equal_to(2)
    assert_that(result.stderr).contains("IMAGES is required")


def test_cosign_sign_images_rejects_tag_refs(tmp_path: Path) -> None:
    """Signing must be refused for refs not pinned by digest."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stub(bin_dir, "cosign", "exit 0")

    result = _run_with_stubs(
        "scripts/ci/cosign-sign-images.sh",
        bin_dir,
        {"IMAGES": "ghcr.io/example/app:main"},
    )

    assert_that(result.returncode).is_equal_to(2)
    assert_that(result.stderr).contains("Refusing to sign non-digest ref")


def test_cosign_sign_images_signs_each_digest(tmp_path: Path) -> None:
    """Every digest-pinned ref should be signed exactly once."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cosign_log = tmp_path / "cosign.log"
    _write_stub(bin_dir, "cosign", 'echo "$*" >> "$COSIGN_LOG"')

    result = _run_with_stubs(
        "scripts/ci/cosign-sign-images.sh",
        bin_dir,
        {
            "IMAGES": (
                "ghcr.io/example/app@sha256:aaa111\n"
                "ghcr.io/example/app-base@sha256:bbb222"
            ),
            "COSIGN_LOG": str(cosign_log),
        },
    )

    assert_that(result.returncode).is_equal_to(0)
    lines = cosign_log.read_text().strip().splitlines()
    assert_that(lines).is_equal_to(
        [
            "sign --yes ghcr.io/example/app@sha256:aaa111",
            "sign --yes ghcr.io/example/app-base@sha256:bbb222",
        ],
    )


def test_delete_ci_ghcr_tags_deletes_only_sole_tag_versions(
    tmp_path: Path,
) -> None:
    """Versions sharing a digest with other tags must be skipped (#1138)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh_log = tmp_path / "gh.log"
    _write_stub(
        bin_dir,
        "gh",
        (
            'echo "$*" >> "$GH_LOG"\n'
            'if [[ "$*" == *"DELETE"* ]]; then\n'
            "  exit 0\n"
            "fi\n"
            'printf "%s\\n" "$GH_VERSIONS_TSV"'
        ),
    )
    # Version 101 shares the digest with a promoted tag; 102 is CI-only.
    versions_tsv = "101\tci-123 main\n102\tci-123"

    result = _run_with_stubs(
        "scripts/ci/maintenance/delete-ci-ghcr-tags.sh",
        bin_dir,
        {
            "CI_TAG": "ci-123",
            "GH_TOKEN": "dummy",  # nosec B105 - fake token for stubbed gh
            "GH_LOG": str(gh_log),
            "GH_VERSIONS_TSV": versions_tsv,
        },
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Skipping version 101")
    assert_that(result.stdout).contains("Deleted version 102")
    log = gh_log.read_text()
    assert_that(log).contains("versions/102")
    assert_that(log).does_not_contain("versions/101")
