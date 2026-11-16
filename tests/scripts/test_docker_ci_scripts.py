"""Tests for docker-ci workflow helper shell scripts."""

from __future__ import annotations

import datetime
import os
import re
import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
import tempfile
from dataclasses import dataclass
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
        "scripts/ci/run-code-quality-gate.sh",
        "scripts/ci/evaluate-code-quality-gate.sh",
        "scripts/ci/assert-required-check.sh",
        "scripts/ci/is-infra-flake-failure.sh",
        "scripts/ci/resolve-docker-rolling-tags.sh",
        "scripts/ci/promote-ci-docker-images.sh",
        "scripts/ci/tag-pinned-tools-digest.sh",
        "scripts/ci/check-tools-manifest-staleness.sh",
        "scripts/ci/cosign-sign-images.sh",
        "scripts/ci/testing/pull-ci-docker-images.sh",
        "scripts/ci/testing/load-ci-docker-images.sh",
        "scripts/ci/testing/pull-lintro-image.sh",
        "scripts/ci/testing/resolve-lintro-image.sh",
        "scripts/ci/maintenance/delete-ci-ghcr-tags.sh",
        "scripts/ci/maintenance/sweep-ci-ghcr-tags.sh",
        "scripts/ci/validate-docker-backfill-inputs.sh",
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
    ("default_branch", "remote_sha", "expected"),
    [
        ("main", "a" * 40, "rolling-tags-enabled=true"),
        ("main", "b" * 40, "rolling-tags-enabled=false"),
        ("release/stable", "a" * 40, "rolling-tags-enabled=true"),
    ],
)
def test_resolve_docker_rolling_tags_compares_current_remote_tip(
    tmp_path: Path,
    default_branch: str,
    remote_sha: str,
    expected: str,
) -> None:
    """Only the current default-branch run may update rolling image tags."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    github_output = tmp_path / "github_output"
    github_output.touch()
    _write_stub(
        bin_dir,
        "git",
        (
            'if [[ "$1" == "check-ref-format" ]]; then exit 0; fi\n'
            'if [[ "$1" == "ls-remote" && "$2" == "--exit-code" '
            f'&& "$3" == "origin" && "$4" == "refs/heads/{default_branch}" ]]; then\n'
            f'  echo "{remote_sha}  refs/heads/{default_branch}"\n'
            "  exit 0\n"
            "fi\n"
            "exit 1"
        ),
    )

    result = _run_with_stubs(
        "scripts/ci/resolve-docker-rolling-tags.sh",
        bin_dir,
        {
            "DEFAULT_BRANCH": default_branch,
            "RUN_SHA": "a" * 40,
            "GITHUB_OUTPUT": str(github_output),
        },
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(github_output.read_text().strip()).is_equal_to(expected)


def test_resolve_docker_rolling_tags_fails_closed_on_lookup_error(
    tmp_path: Path,
) -> None:
    """A remote lookup failure skips rolling tags without blocking SHA tags."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    github_output = tmp_path / "github_output"
    github_output.touch()
    _write_stub(
        bin_dir,
        "git",
        'if [[ "$1" == "check-ref-format" ]]; then exit 0; fi\nexit 1',
    )

    result = _run_with_stubs(
        "scripts/ci/resolve-docker-rolling-tags.sh",
        bin_dir,
        {
            "DEFAULT_BRANCH": "main",
            "RUN_SHA": "a" * 40,
            "GITHUB_OUTPUT": str(github_output),
        },
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(github_output.read_text().strip()).is_equal_to(
        "rolling-tags-enabled=false",
    )


@pytest.mark.parametrize(
    ("default_branch", "run_sha", "git_stub"),
    [
        ("", "a" * 40, "exit 1"),
        ("main", "", "exit 1"),
        (
            "main",
            "not-a-sha",
            'if [[ "$1" == "check-ref-format" ]]; then exit 0; fi\nexit 1',
        ),
        ("invalid branch", "a" * 40, "exit 1"),
        (
            "main",
            "a" * 40,
            (
                'if [[ "$1" == "check-ref-format" ]]; then exit 0; fi\n'
                'if [[ "$1" == "ls-remote" && "$2" == "--exit-code" '
                '&& "$3" == "origin" && "$4" == "refs/heads/main" ]]; then\n'
                '  echo "not-a-sha  refs/heads/main"\n'
                "  exit 0\n"
                "fi\n"
                "exit 1\n"
            ),
        ),
    ],
)
def test_resolve_docker_rolling_tags_fails_closed_on_invalid_inputs(
    tmp_path: Path,
    default_branch: str,
    run_sha: str,
    git_stub: str,
) -> None:
    """Missing or malformed inputs never enable a rolling image tag."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    github_output = tmp_path / "github_output"
    github_output.touch()
    _write_stub(bin_dir, "git", git_stub)

    result = _run_with_stubs(
        "scripts/ci/resolve-docker-rolling-tags.sh",
        bin_dir,
        {
            "DEFAULT_BRANCH": default_branch,
            "RUN_SHA": run_sha,
            "GITHUB_OUTPUT": str(github_output),
        },
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(github_output.read_text().strip()).is_equal_to(
        "rolling-tags-enabled=false",
    )


@pytest.mark.parametrize(
    "missing",
    ["SOURCE_IMAGE", "TAGS"],
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


def test_promote_ci_docker_images_requires_ci_tag_or_source_digest() -> None:
    """A promote without CI_TAG or SOURCE_DIGEST must fail closed."""
    script_path = (_REPO_ROOT / "scripts/ci/promote-ci-docker-images.sh").resolve()
    result = subprocess.run(  # nosec B603 - fixed argv run against a repo script
        [str(script_path)],
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ.copy(),
            "SOURCE_IMAGE": "ghcr.io/example/app",
            "CI_TAG": "",
            "SOURCE_DIGEST": "",
            "TAGS": "ghcr.io/example/app:main",
        },
    )
    assert_that(result.returncode).is_equal_to(2)
    assert_that(result.stderr).contains("CI_TAG or SOURCE_DIGEST is required")


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
        "buildx imagetools create --prefer-index=false "
        "ghcr.io/example/app@sha256:aaa111 "
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


def test_promote_ci_docker_images_retries_transient_promote_error(
    tmp_path: Path,
) -> None:
    """A transient CDN error during the retag is retried and then succeeds."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker_log = tmp_path / "docker.log"
    # inspect always resolves the digest; the first two `create` calls flake
    # with the exact registry-CDN signature from run 30097353380, the third
    # succeeds. The verify inspects then confirm the promoted digest matches.
    _write_stub(
        bin_dir,
        "docker",
        (
            'echo "$*" >> "$DOCKER_LOG"\n'
            'if [[ "$*" == *" inspect "* ]]; then\n'
            '  echo "sha256:aaa111"\n'
            "  exit 0\n"
            "fi\n"
            'if [[ "$*" == *" create "* ]]; then\n'
            '  n="$(grep -c " create " "$DOCKER_LOG")"\n'
            "  if (( n < 3 )); then\n"
            '    echo "ERROR: httpReadSeeker: failed open: read tcp'
            " 10.1.1.153->185.199.108.154:443: read: connection reset by"
            ' peer" >&2\n'
            "    exit 1\n"
            "  fi\n"
            "  exit 0\n"
            "fi\n"
            "exit 0"
        ),
    )

    result = _run_with_stubs(
        "scripts/ci/promote-ci-docker-images.sh",
        bin_dir,
        {
            "SOURCE_IMAGE": "ghcr.io/example/app",
            "CI_TAG": "ci-1",
            "TAGS": "ghcr.io/example/app:main",
            "DOCKER_LOG": str(docker_log),
            "PROMOTE_BACKOFF_SECONDS": "0",
            "PROMOTE_MAX_ATTEMPTS": "4",
        },
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stderr).contains("Transient registry error")
    # Two flakes + one success == three create attempts.
    create_calls = docker_log.read_text().count("imagetools create")
    assert_that(create_calls).is_equal_to(3)


def test_promote_ci_docker_images_retries_transient_inspect_error(
    tmp_path: Path,
) -> None:
    """A transient error during the source digest resolve is retried too.

    The retry wraps every registry call, not just the retag — the source
    digest `inspect` must recover from a transient blip as well.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker_log = tmp_path / "docker.log"
    # The first inspect (source digest resolve) flakes once, then every
    # inspect resolves the digest and create succeeds.
    _write_stub(
        bin_dir,
        "docker",
        (
            'echo "$*" >> "$DOCKER_LOG"\n'
            'if [[ "$*" == *" inspect "* ]]; then\n'
            '  n="$(grep -c " inspect " "$DOCKER_LOG")"\n'
            "  if (( n == 1 )); then\n"
            '    echo "ERROR: httpReadSeeker: failed open: read: connection'
            ' reset by peer" >&2\n'
            "    exit 1\n"
            "  fi\n"
            '  echo "sha256:aaa111"\n'
            "  exit 0\n"
            "fi\n"
            "exit 0"
        ),
    )

    result = _run_with_stubs(
        "scripts/ci/promote-ci-docker-images.sh",
        bin_dir,
        {
            "SOURCE_IMAGE": "ghcr.io/example/app",
            "CI_TAG": "ci-1",
            "TAGS": "ghcr.io/example/app:main",
            "DOCKER_LOG": str(docker_log),
            "PROMOTE_BACKOFF_SECONDS": "0",
            "PROMOTE_MAX_ATTEMPTS": "4",
        },
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stderr).contains("Transient registry error")


def test_promote_ci_docker_images_rejects_non_numeric_attempts(
    tmp_path: Path,
) -> None:
    """A non-numeric PROMOTE_MAX_ATTEMPTS fails fast before any registry call."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker_log = tmp_path / "docker.log"
    _write_stub(bin_dir, "docker", 'echo "$*" >> "$DOCKER_LOG"\nexit 0')

    result = _run_with_stubs(
        "scripts/ci/promote-ci-docker-images.sh",
        bin_dir,
        {
            "SOURCE_IMAGE": "ghcr.io/example/app",
            "CI_TAG": "ci-1",
            "TAGS": "ghcr.io/example/app:main",
            "PROMOTE_MAX_ATTEMPTS": "not-a-number",
            "DOCKER_LOG": str(docker_log),
        },
    )

    assert_that(result.returncode).is_equal_to(2)
    assert_that(result.stderr).contains("PROMOTE_MAX_ATTEMPTS must be")
    assert_that(docker_log.exists()).is_false()


def test_promote_ci_docker_images_does_not_retry_fatal_error(
    tmp_path: Path,
) -> None:
    """A genuine denied/auth error fails on the first attempt (no retry)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker_log = tmp_path / "docker.log"
    _write_stub(
        bin_dir,
        "docker",
        (
            'echo "$*" >> "$DOCKER_LOG"\n'
            'if [[ "$*" == *" inspect "* ]]; then\n'
            '  echo "sha256:aaa111"\n'
            "  exit 0\n"
            "fi\n"
            'if [[ "$*" == *" create "* ]]; then\n'
            '  echo "denied: requested access to the resource is denied"'
            " >&2\n"
            "  exit 1\n"
            "fi\n"
            "exit 0"
        ),
    )

    result = _run_with_stubs(
        "scripts/ci/promote-ci-docker-images.sh",
        bin_dir,
        {
            "SOURCE_IMAGE": "ghcr.io/example/app",
            "CI_TAG": "ci-1",
            "TAGS": "ghcr.io/example/app:main",
            "DOCKER_LOG": str(docker_log),
            "PROMOTE_BACKOFF_SECONDS": "0",
            "PROMOTE_MAX_ATTEMPTS": "4",
        },
    )

    assert_that(result.returncode).is_not_equal_to(0)
    assert_that(result.stderr).does_not_contain("Transient registry error")
    create_calls = docker_log.read_text().count("imagetools create")
    assert_that(create_calls).is_equal_to(1)


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
                f"ghcr.io/example/app@sha256:{'a' * 64}\n"
                f"ghcr.io/example/app-base@sha256:{'b' * 64}"
            ),
            "COSIGN_LOG": str(cosign_log),
        },
    )

    assert_that(result.returncode).is_equal_to(0)
    lines = cosign_log.read_text().strip().splitlines()
    assert_that(lines).is_equal_to(
        [
            f"sign --yes ghcr.io/example/app@sha256:{'a' * 64}",
            f"sign --yes ghcr.io/example/app-base@sha256:{'b' * 64}",
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
            'if [[ "$*" == *"/versions/"* ]]; then\n'
            '  echo "$GH_VERSION_TAGS"\n'
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
            # Pre-delete re-check still sees only the CI tag on 102.
            "GH_VERSION_TAGS": "ci-123",
        },
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Skipping version 101")
    assert_that(result.stdout).contains("Deleted version 102")
    log = gh_log.read_text()
    assert_that(log).contains("--method DELETE")
    assert_that(log).contains("versions/102")
    assert_that(log).does_not_contain("versions/101")


def test_delete_ci_ghcr_tags_recheck_catches_new_tags(
    tmp_path: Path,
) -> None:
    """A tag attached between snapshot and delete must abort the delete."""
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
            'if [[ "$*" == *"/versions/"* ]]; then\n'
            '  echo "$GH_VERSION_TAGS"\n'
            "  exit 0\n"
            "fi\n"
            'printf "%s\\n" "$GH_VERSIONS_TSV"'
        ),
    )

    result = _run_with_stubs(
        "scripts/ci/maintenance/delete-ci-ghcr-tags.sh",
        bin_dir,
        {
            "CI_TAG": "ci-123",
            "GH_TOKEN": "dummy",  # nosec B105 - fake token for stubbed gh
            "GH_LOG": str(gh_log),
            # Snapshot sees a sole-tag version...
            "GH_VERSIONS_TSV": "102\tci-123",
            # ...but a concurrent run attached a tag before the delete.
            "GH_VERSION_TAGS": "ci-123 sha-def",
        },
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("tags changed since snapshot")
    assert_that(gh_log.read_text()).does_not_contain("--method DELETE")


def test_sweep_ci_ghcr_tags_requires_token() -> None:
    """sweep-ci-ghcr-tags.sh should fail when GH_TOKEN is missing."""
    script_path = (
        _REPO_ROOT / "scripts/ci/maintenance/sweep-ci-ghcr-tags.sh"
    ).resolve()
    result = subprocess.run(  # nosec B603 - fixed argv run against a repo script in a controlled test; shell=False, no user shell input
        [str(script_path)],
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ.copy(),
            "GH_TOKEN": "",
        },  # nosec B105 - empty token for required-env validation
    )
    assert_that(result.returncode).is_equal_to(2)
    assert_that(result.stderr).contains("GH_TOKEN is required")


def test_sweep_ci_ghcr_tags_validates_min_age_days() -> None:
    """sweep-ci-ghcr-tags.sh should reject a non-integer MIN_AGE_DAYS."""
    script_path = (
        _REPO_ROOT / "scripts/ci/maintenance/sweep-ci-ghcr-tags.sh"
    ).resolve()
    result = subprocess.run(  # nosec B603 - fixed argv run against a repo script in a controlled test; shell=False, no user shell input
        [str(script_path)],
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ.copy(),
            "GH_TOKEN": "x",  # nosec B105 - placeholder token for arg validation
            "MIN_AGE_DAYS": "notanumber",
        },
    )
    assert_that(result.returncode).is_equal_to(2)
    assert_that(result.stderr).contains("MIN_AGE_DAYS must be")


def _run_sweep_validation(
    tmp_path: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    """Run the GHCR sweep with a no-op ``gh`` stub so no network call happens.

    The stub prints nothing, so any invocation that reaches the sweep body
    finds no candidate versions and exits 0. Only input validation is
    exercised.

    Sweep-specific variables the caller does not set are blanked rather than
    inherited, so an ambient ``MIN_AGE_DAYS``/``TAG_PREFIX`` in the developer
    or CI environment cannot mask the script's own defaults.

    Args:
        tmp_path: Pytest temporary directory for the PATH shim.
        env: Extra environment variables for the script.

    Returns:
        subprocess.CompletedProcess[str]: The completed process.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stub(bin_dir, "gh", "exit 0")
    scrubbed = {
        name: ""
        for name in (
            "MIN_AGE_DAYS",
            "TAG_PREFIX",
            "ALLOW_SHORT_RETENTION",
            "DRY_RUN",
            "VOLATILE_TAG_PREFIXES",
            "TIME_BUDGET_SECONDS",
            "VERIFY_RESERVE_SECONDS",
            "GH_CALL_TIMEOUT_SECONDS",
            "MAX_DELETES",
            "PAGE_SIZE",
            "PACKAGE_ROTATION",
            "GITHUB_STEP_SUMMARY",
        )
        if name not in env
    }
    return _run_with_stubs(
        "scripts/ci/maintenance/sweep-ci-ghcr-tags.sh",
        bin_dir,
        {
            "GH_TOKEN": "dummy",  # nosec B105 - fake token, gh is stubbed
            "PACKAGES": "py-lintro",
            **scrubbed,
            **env,
        },
    )


@pytest.mark.parametrize("min_age_days", ["0", "1", "90"])
def test_sweep_ci_ghcr_tags_rejects_short_retention(
    tmp_path: Path,
    min_age_days: str,
) -> None:
    """MIN_AGE_DAYS below the 91d rerun window must be rejected (#1138).

    90 pins the boundary so a regressed floor cannot pass unnoticed.
    """
    result = _run_sweep_validation(tmp_path, {"MIN_AGE_DAYS": min_age_days})

    assert_that(result.returncode).is_equal_to(2)
    assert_that(result.stderr).contains("below the 91d")
    assert_that(result.stderr).contains("rerun-retention window")
    assert_that(result.stderr).contains("ALLOW_SHORT_RETENTION=true")


def test_sweep_ci_ghcr_tags_short_retention_override(tmp_path: Path) -> None:
    """ALLOW_SHORT_RETENTION=true permits a sub-floor MIN_AGE_DAYS, loudly."""
    result = _run_sweep_validation(
        tmp_path,
        {"MIN_AGE_DAYS": "0", "ALLOW_SHORT_RETENTION": "true"},
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Sweeping ci-* tags older than 0d")
    # The bypass must never be silent: an inherited value has to be visible.
    assert_that(result.stderr).contains("::warning::ALLOW_SHORT_RETENTION=true")
    assert_that(result.stderr).contains("waives the 91d guard")


def test_sweep_ci_ghcr_tags_accepts_floor_value(tmp_path: Path) -> None:
    """MIN_AGE_DAYS equal to the 91d floor must pass validation."""
    result = _run_sweep_validation(tmp_path, {"MIN_AGE_DAYS": "91"})

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Sweeping ci-* tags older than 91d")


def test_sweep_ci_ghcr_tags_default_min_age_is_the_floor(tmp_path: Path) -> None:
    """With MIN_AGE_DAYS unset the script must default to the 91d floor."""
    result = _run_sweep_validation(tmp_path, {})

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Sweeping ci-* tags older than 91d")


@pytest.mark.parametrize(
    "tag_prefix",
    [
        'ci-" or true or "',
        "ci-$(id)",
        "ci-*",
        "ci with space",
    ],
)
def test_sweep_ci_ghcr_tags_rejects_unsafe_tag_prefix(
    tmp_path: Path,
    tag_prefix: str,
) -> None:
    """TAG_PREFIX is interpolated into jq, so unsafe characters must fail."""
    result = _run_sweep_validation(tmp_path, {"TAG_PREFIX": tag_prefix})

    assert_that(result.returncode).is_equal_to(2)
    assert_that(result.stderr).contains("TAG_PREFIX must match")


@pytest.mark.parametrize("tag_prefix", ["ci-", "ci", "ci.1_0-x"])
def test_sweep_ci_ghcr_tags_accepts_safe_tag_prefix(
    tmp_path: Path,
    tag_prefix: str,
) -> None:
    """A TAG_PREFIX within the allowed character class must pass validation."""
    result = _run_sweep_validation(tmp_path, {"TAG_PREFIX": tag_prefix})

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains(f"Sweeping {tag_prefix}* tags")


#: Fake ``gh`` for the sweep (#1138, #1652, #3492). It serves the paged
#: listing (``versions?per_page=N&page=P``) by slicing ``$GH_VERSIONS`` rows
#: (``id\tupdated_at\tepoch\ttags``), or ``$GH_LIST_<n>`` for the n-th listing
#: request; fails listing request n when n is in ``$GH_LIST_FAIL``; answers a
#: recheck from ``$GH_STATE_<id>`` (default: the version's row), or fails it
#: with ``$GH_RECHECK_FAIL_<id>`` on stderr. With ``$CLOCK_FILE`` set, every
#: call advances the fake clock: listing requests by ``$GH_LIST_COST``
#: (per package via ``$GH_LIST_COSTS="pkg=s ..."``, or ``$GH_LIST_SLOW_COST``
#: from request ``$GH_LIST_SLOW_FROM`` on), deletes by ``$GH_DELETE_COST``.
_SWEEP_GH_STUB = r"""
echo "$*" >> "$GH_LOG"
clock_add() {
  [[ -n "${CLOCK_FILE:-}" ]] || return 0
  echo $(( $(cat "$CLOCK_FILE") + $1 )) > "$CLOCK_FILE"
}
if [[ -n "${GH_SLEEP:-}" ]]; then
  exec sleep "$GH_SLEEP"
fi
if [[ "$*" == *"--method DELETE"* ]]; then
  clock_add "${GH_DELETE_COST:-0}"
  exit 0
fi
list_re='/packages/container/([^/]+)/versions[?]per_page=([0-9]+)&page=([0-9]+)'
if [[ "$*" =~ $list_re ]]; then
  pkg="${BASH_REMATCH[1]}"
  per="${BASH_REMATCH[2]}"
  page="${BASH_REMATCH[3]}"
  n=$(( $(cat "$GH_LIST_COUNT" 2>/dev/null || echo 0) + 1 ))
  echo "$n" > "$GH_LIST_COUNT"
  cost="${GH_LIST_COST:-0}"
  for spec in ${GH_LIST_COSTS:-}; do
    if [[ "${spec%%=*}" == "$pkg" ]]; then cost="${spec#*=}"; fi
  done
  if [[ -n "${GH_LIST_SLOW_FROM:-}" ]] && (( n >= GH_LIST_SLOW_FROM )); then
    cost="$GH_LIST_SLOW_COST"
  fi
  clock_add "$cost"
  case " ${GH_LIST_FAIL:-} " in
  *" $n "*) echo "API rate limit exceeded" >&2; exit 1 ;;
  esac
  var="GH_LIST_${n}"
  rows="${!var:-$GH_VERSIONS}"
  printf '%s\n' "$rows" |
    awk -v s="$(( (page - 1) * per ))" -v p="$per" 'NF && ++i > s && i <= s + p'
  exit 0
fi
if [[ "$*" =~ /versions/([0-9]+) ]]; then
  vid="${BASH_REMATCH[1]}"
  fail_var="GH_RECHECK_FAIL_${vid}"
  if [[ -n "${!fail_var:-}" ]]; then
    echo "${!fail_var}" >&2
    exit 1
  fi
  state_var="GH_STATE_${vid}"
  if [[ -n "${!state_var:-}" ]]; then
    printf '%s\n' "${!state_var}"
  else
    printf '%s\n' "$GH_VERSIONS" |
      awk -F'\t' -v id="$vid" '$1 == id { print $2 "\t" $4 }'
  fi
  exit 0
fi
echo "unexpected gh call: $*" >&2
exit 99
"""

#: Sweep knobs blanked by default so the developer's or CI's environment
#: (``GITHUB_STEP_SUMMARY`` is set on every Actions runner) cannot leak in.
_SWEEP_KNOBS = (
    "TAG_PREFIX",
    "DRY_RUN",
    "VOLATILE_TAG_PREFIXES",
    "TIME_BUDGET_SECONDS",
    "VERIFY_RESERVE_SECONDS",
    "GH_CALL_TIMEOUT_SECONDS",
    "MAX_DELETES",
    "PAGE_SIZE",
    "PACKAGE_ROTATION",
    "GITHUB_STEP_SUMMARY",
)


def _version_row(vid: int, tags: str, updated_at: str = "2026-01-02T00:00:00Z") -> str:
    """Build one listing row the way the script's jq program prints it.

    Args:
        vid: Package version id.
        tags: Space-joined tags ("" for an untagged version).
        updated_at: ISO-8601 UTC timestamp.

    Returns:
        str: The tab-separated id, updated_at, epoch and tags.
    """
    epoch = int(
        datetime.datetime.strptime(updated_at, "%Y-%m-%dT%H:%M:%SZ")
        .replace(tzinfo=datetime.UTC)
        .timestamp(),
    )
    return f"{vid}\t{updated_at}\t{epoch}\t{tags}"


def _rows(*rows: str) -> str:
    """Join listing rows into the newline-separated ``GH_VERSIONS`` value.

    Args:
        *rows: Rows from :func:`_version_row`.

    Returns:
        str: The joined rows.
    """
    return "\n".join(rows)


#: Persistent tags every fixture package carries.
_PERSISTENT_ROWS = (_version_row(900, "latest"), _version_row(901, "0.9.9"))


def _sole_ci_rows(*vids: int) -> tuple[str, ...]:
    """Aged, sole-tagged CI versions: the only deletable kind.

    Args:
        *vids: Version ids.

    Returns:
        tuple[str, ...]: One row per id, tagged ``ci-<id>``.
    """
    return tuple(_version_row(vid, f"ci-{vid}") for vid in vids)


@dataclass(frozen=True)
class _SweepRun:
    """Outcome of one sweep run against the fake ``gh``.

    Attributes:
        result: The completed process.
        gh_log: Every ``gh`` argv, one per line.
        list_calls: Number of listing requests made.
        clock: Final fake-clock reading minus the start, or None without one.
    """

    result: subprocess.CompletedProcess[str]
    gh_log: str
    list_calls: int
    clock: int | None


_CLOCK_START = 1_900_000_000


def _run_sweep(
    work_dir: Path,
    env: dict[str, str],
    *,
    clock: bool = False,
) -> _SweepRun:
    """Run the sweep against :data:`_SWEEP_GH_STUB` in ``work_dir``.

    Args:
        work_dir: Fresh directory for the PATH shim and the stub's state.
        env: Variables layered over the defaults (one package, ages waived).
        clock: Shadow ``date`` with a fake clock the stub advances.

    Returns:
        _SweepRun: The run's outcome.
    """
    bin_dir = work_dir / "bin"
    bin_dir.mkdir(parents=True)
    _write_stub(bin_dir, "gh", _SWEEP_GH_STUB)
    gh_log = work_dir / "gh.log"
    gh_log.touch()
    list_count = work_dir / "list.count"
    clock_file = work_dir / "clock"
    base = {
        **dict.fromkeys(_SWEEP_KNOBS, ""),
        "GH_TOKEN": "dummy",  # nosec B105 - fake token for stubbed gh
        "PACKAGES": "py-lintro",
        "MIN_AGE_DAYS": "0",
        "ALLOW_SHORT_RETENTION": "true",
        "GH_LOG": str(gh_log),
        "GH_LIST_COUNT": str(list_count),
        "GH_VERSIONS": _rows(*_PERSISTENT_ROWS, *_sole_ci_rows(202)),
    }
    if clock:
        _write_stub(bin_dir, "date", 'cat "$CLOCK_FILE"')
        clock_file.write_text(f"{_CLOCK_START}\n")
        base["CLOCK_FILE"] = str(clock_file)
    result = _run_with_stubs(
        "scripts/ci/maintenance/sweep-ci-ghcr-tags.sh",
        bin_dir,
        {**base, **env},
    )
    return _SweepRun(
        result=result,
        gh_log=gh_log.read_text(),
        list_calls=int(list_count.read_text()) if list_count.exists() else 0,
        clock=int(clock_file.read_text()) - _CLOCK_START if clock else None,
    )


def test_sweep_ci_ghcr_tags_deletes_only_aged_sole_ci_versions(
    tmp_path: Path,
) -> None:
    """Only aged versions whose sole tag is a CI tag are deleted (#1138)."""
    run = _run_sweep(
        tmp_path,
        {
            "GH_VERSIONS": _rows(
                *_PERSISTENT_ROWS,
                _version_row(201, "ci-1 ci-2"),
                _version_row(202, "ci-old"),
                _version_row(203, "ci-mixed 0.9.8"),
                _version_row(204, ""),
                _version_row(205, "ci-young", "2999-01-01T00:00:00Z"),
            ),
        },
    )

    assert_that(run.result.returncode).is_equal_to(0)
    assert_that(run.result.stdout).contains("Deleted py-lintro version 202")
    assert_that(run.result.stdout).contains("not sole-tagged")
    assert_that(run.result.stdout).contains("Sweep complete: 1 deleted")
    # Two rechecks, then the DELETE, for 202 alone.
    assert_that(run.gh_log.count("py-lintro/versions/202")).is_equal_to(3)
    for vid in (201, 203, 204, 205):
        assert_that(run.gh_log).does_not_contain(f"py-lintro/versions/{vid}")


@pytest.mark.parametrize(
    ("recheck_env", "expected"),
    [
        # Promotion attached main between snapshot and delete.
        (
            {"GH_STATE_202": "2026-01-02T00:00:00Z\tci-202 main"},
            "tags changed since snapshot",
        ),
        (
            {"GH_STATE_202": "2026-01-03T00:00:00Z\tci-202"},
            "updated_at changed since snapshot",
        ),
        # A concurrent build attached another ci-* tag.
        (
            {"GH_STATE_202": "2026-01-02T00:00:00Z\tci-202 ci-new"},
            "no longer sole-tagged",
        ),
        # Already deleted by a prior sweep or a manual cleanup: benign.
        ({"GH_RECHECK_FAIL_202": "gh: Not Found (HTTP 404)"}, "already deleted"),
    ],
)
def test_sweep_ci_ghcr_tags_recheck_aborts_the_delete(
    tmp_path: Path,
    recheck_env: dict[str, str],
    expected: str,
) -> None:
    """A version that changed (or vanished) since the listing is not deleted."""
    run = _run_sweep(tmp_path, recheck_env)

    assert_that(run.result.returncode).is_equal_to(0)
    assert_that(run.result.stdout).contains(expected)
    assert_that(run.result.stderr).does_not_contain("Failed to re-check")
    assert_that(run.gh_log).does_not_contain("--method DELETE")


def test_sweep_ci_ghcr_tags_recheck_hard_error_fails(tmp_path: Path) -> None:
    """Non-404 recheck failures must fail closed (auth/network/rate-limit)."""
    run = _run_sweep(tmp_path, {"GH_RECHECK_FAIL_202": "API rate limit exceeded"})

    assert_that(run.result.returncode).is_equal_to(1)
    assert_that(run.result.stderr).contains("Failed to re-check")
    assert_that(run.gh_log).does_not_contain("--method DELETE")


def test_sweep_ci_ghcr_tags_fails_on_query_error(tmp_path: Path) -> None:
    """Package version query failures must fail the sweep job."""
    run = _run_sweep(tmp_path, {"GH_LIST_FAIL": "1"})

    assert_that(run.result.returncode).is_equal_to(1)
    assert_that(run.result.stderr).contains("Failed to query versions")
    assert_that(run.gh_log).does_not_contain("--method DELETE")


def test_sweep_ci_ghcr_tags_deletes_nothing_when_a_later_page_fails(
    tmp_path: Path,
) -> None:
    """A listing that breaks mid-pagination is incomplete, so nothing is deleted.

    The baseline comes from the same listing, so a partial one would leave the
    post-delete verification blind to tags on the pages never read (#1652).
    """
    run = _run_sweep(tmp_path, {"PAGE_SIZE": "1", "GH_LIST_FAIL": "2"})

    assert_that(run.result.returncode).is_equal_to(1)
    assert_that(run.result.stderr).contains("Failed to query versions")
    assert_that(run.gh_log).contains("per_page=1&page=2")
    assert_that(run.gh_log).does_not_contain("--method DELETE")


def test_sweep_ci_ghcr_tags_pages_through_the_listing(tmp_path: Path) -> None:
    """Candidates on any page are found, and paging stops at a short page."""
    run = _run_sweep(
        tmp_path,
        {
            "PAGE_SIZE": "2",
            "GH_VERSIONS": _rows(*_PERSISTENT_ROWS, *_sole_ci_rows(301, 302, 303)),
        },
    )

    assert_that(run.result.returncode).is_equal_to(0)
    assert_that(run.result.stdout).contains("Sweep complete: 3 deleted")
    # Five rows at two per page: pages 1-3 for the listing and again for the
    # verification; the short third page ends each pass.
    assert_that(run.list_calls).is_equal_to(6)
    assert_that(run.gh_log).does_not_contain("page=4")


def test_sweep_ci_ghcr_tags_dry_run_skips_delete(tmp_path: Path) -> None:
    """DRY_RUN=true should log candidates without calling DELETE."""
    run = _run_sweep(tmp_path, {"DRY_RUN": "true"})

    assert_that(run.result.returncode).is_equal_to(0)
    assert_that(run.result.stdout).contains("[dry-run] Would delete")
    assert_that(run.gh_log).does_not_contain("--method DELETE")
    # A dry run deletes nothing, so it pays for no verification listing.
    assert_that(run.list_calls).is_equal_to(1)


def test_sweep_ci_ghcr_tags_verifies_persistent_tags_after_delete(
    tmp_path: Path,
) -> None:
    """A clean sweep still re-reads the persistent tags and stays green (#1652)."""
    run = _run_sweep(tmp_path, {})

    assert_that(run.result.returncode).is_equal_to(0)
    assert_that(run.result.stdout).contains("Deleted py-lintro version 202")
    assert_that(run.result.stderr).does_not_contain("Persistent tags disappeared")
    # The listing doubles as the baseline; one confirmation after the delete.
    assert_that(run.list_calls).is_equal_to(2)


#: The package after the delete with the 0.9.9 release tag gone.
_LISTING_WITHOUT_RELEASE = _rows(_PERSISTENT_ROWS[0])


def test_sweep_ci_ghcr_tags_alerts_when_a_persistent_tag_disappears(
    tmp_path: Path,
) -> None:
    """A release tag lost to the recheck/DELETE race must fail the job (#1652).

    This is the residual TOCTOU the sole-tag rule and dual recheck cannot
    close: GHCR has no conditional delete, so a promotion landing in the
    window takes its tag down with the CI version.
    """
    run = _run_sweep(
        tmp_path,
        {"GH_LIST_2": _LISTING_WITHOUT_RELEASE, "GH_LIST_3": _LISTING_WITHOUT_RELEASE},
    )

    assert_that(run.result.returncode).is_equal_to(1)
    assert_that(run.result.stdout).contains("Deleted py-lintro version 202")
    assert_that(run.result.stderr).contains(
        "Persistent tags disappeared from py-lintro",
    )
    assert_that(run.result.stderr).contains("0.9.9")
    assert_that(run.result.stderr).contains("202")


def test_sweep_ci_ghcr_tags_tolerates_a_transient_listing_gap(
    tmp_path: Path,
) -> None:
    """A tag missing from one read only is not reported (#1652).

    A paginated listing on a busy registry can momentarily under-report, and a
    false "we deleted a release tag" alarm is worse than no alarm at all.
    """
    run = _run_sweep(tmp_path, {"GH_LIST_2": _LISTING_WITHOUT_RELEASE})

    assert_that(run.result.returncode).is_equal_to(0)
    assert_that(run.result.stderr).does_not_contain("Persistent tags disappeared")
    assert_that(run.list_calls).is_equal_to(3)


def test_sweep_ci_ghcr_tags_fails_when_verification_cannot_read(
    tmp_path: Path,
) -> None:
    """An unreadable post-delete listing fails closed without claiming a loss."""
    run = _run_sweep(tmp_path, {"GH_LIST_FAIL": "2"})

    assert_that(run.result.returncode).is_equal_to(1)
    assert_that(run.result.stderr).contains("Could not verify persistent tags")
    # Never accuse the sweep of a deletion it did not observe.
    assert_that(run.result.stderr).does_not_contain("Persistent tags disappeared")


def test_sweep_ci_ghcr_tags_fails_when_the_confirmation_read_cannot_run(
    tmp_path: Path,
) -> None:
    """A failed confirmation read must not be treated as proof of loss."""
    run = _run_sweep(
        tmp_path,
        {"GH_LIST_2": _LISTING_WITHOUT_RELEASE, "GH_LIST_FAIL": "3"},
    )

    assert_that(run.result.returncode).is_equal_to(1)
    assert_that(run.result.stderr).contains("Could not confirm persistent tags")
    assert_that(run.result.stderr).does_not_contain("Persistent tags disappeared")


@pytest.mark.parametrize(
    ("volatile_prefixes", "expected_returncode"),
    [
        ("", 0),  # default "ci- sha-": the sibling sha-* sweep's deletion
        ("ci-", 1),  # sha- not volatile: the vanished sha tag is reported
    ],
)
def test_sweep_ci_ghcr_tags_baseline_excludes_volatile_prefixes(
    tmp_path: Path,
    volatile_prefixes: str,
    expected_returncode: int,
) -> None:
    """The baseline must ignore tag families a sibling sweep may delete (#1652).

    ghcr-cleanup.yml runs the ci-* and sha-* sweeps as parallel jobs. Without
    the exclusion the ci-* sweep would report the sha-* sweep's legitimate
    deletions as lost release tags — exactly the false alarm to avoid.
    """
    with_sha = _rows(*_PERSISTENT_ROWS, _version_row(950, "sha-abc"))
    run = _run_sweep(
        tmp_path,
        {
            "VOLATILE_TAG_PREFIXES": volatile_prefixes,
            "GH_VERSIONS": _rows(with_sha, *_sole_ci_rows(202)),
            "GH_LIST_2": _rows(*_PERSISTENT_ROWS),
            "GH_LIST_3": _rows(*_PERSISTENT_ROWS),
        },
    )

    assert_that(run.result.returncode).is_equal_to(expected_returncode)
    assert_that(run.result.stdout).contains("Deleted py-lintro version 202")


def test_sweep_ci_ghcr_tags_rejects_unsafe_volatile_prefix(
    tmp_path: Path,
) -> None:
    """VOLATILE_TAG_PREFIXES entries must stay inside the tag character set."""
    result = _run_sweep_validation(
        tmp_path,
        {"VOLATILE_TAG_PREFIXES": 'ci-" or true or "'},
    )

    assert_that(result.returncode).is_equal_to(2)
    assert_that(result.stderr).contains("VOLATILE_TAG_PREFIXES entries must match")


# --- Per-run bound (#3492) ----------------------------------------------------


@pytest.mark.parametrize(
    ("knobs", "expected"),
    [
        ({"TIME_BUDGET_SECONDS": "ten"}, "TIME_BUDGET_SECONDS must be a non-negative"),
        ({"TIME_BUDGET_SECONDS": "-1"}, "TIME_BUDGET_SECONDS must be a non-negative"),
        ({"MAX_DELETES": "1.5"}, "MAX_DELETES must be a non-negative integer"),
        ({"MAX_DELETES": "1234567890"}, "MAX_DELETES must be a non-negative"),
        ({"MAX_DELETES": "100001"}, "MAX_DELETES must be between 0 and 100000"),
        (
            {"TIME_BUDGET_SECONDS": "86401"},
            "TIME_BUDGET_SECONDS must be between 0 and 86400",
        ),
        ({"PAGE_SIZE": "0"}, "PAGE_SIZE must be between 1 and 100"),
        ({"PAGE_SIZE": "101"}, "PAGE_SIZE must be between 1 and 100"),
        (
            {"GH_CALL_TIMEOUT_SECONDS": "0"},
            "GH_CALL_TIMEOUT_SECONDS must be between 1 and 3600",
        ),
        ({"PACKAGE_ROTATION": "x"}, "PACKAGE_ROTATION must be a non-negative"),
        ({"MIN_AGE_DAYS": "36501"}, "MIN_AGE_DAYS must be between 0 and 36500"),
        (
            {"TIME_BUDGET_SECONDS": "300", "VERIFY_RESERVE_SECONDS": "300"},
            "must be below TIME_BUDGET_SECONDS (300)",
        ),
        (
            {"VERIFY_RESERVE_SECONDS": "100", "GH_CALL_TIMEOUT_SECONDS": "60"},
            "must be at least 3 x GH_CALL_TIMEOUT_SECONDS (60)",
        ),
    ],
)
def test_sweep_ci_ghcr_tags_rejects_invalid_run_bound(
    tmp_path: Path,
    knobs: dict[str, str],
    expected: str,
) -> None:
    """A malformed or out-of-range bound fails validation instead of running."""
    result = _run_sweep_validation(tmp_path, knobs)

    assert_that(result.returncode).is_equal_to(2)
    assert_that(result.stderr).contains(expected)


def test_sweep_ci_ghcr_tags_is_bounded_by_default(tmp_path: Path) -> None:
    """With no knobs set, the sweep still runs under a budget and a cap (#3492)."""
    result = _run_sweep_validation(tmp_path, {})

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("time_budget=1200s, max_deletes=500")
    assert_that(result.stdout).contains("Sweep complete: 0 deleted")


@pytest.mark.parametrize("max_deletes", ["08", "09", "0003"])
def test_sweep_ci_ghcr_tags_reads_a_leading_zero_cap_as_decimal(
    tmp_path: Path,
    max_deletes: str,
) -> None:
    """``08`` means eight, not invalid octal that silently disables the cap."""
    run = _run_sweep(
        tmp_path,
        {
            "MAX_DELETES": max_deletes,
            "GH_VERSIONS": _rows(*_PERSISTENT_ROWS, *_sole_ci_rows(*range(301, 313))),
        },
    )

    cap = int(max_deletes)
    assert_that(run.result.returncode).is_equal_to(0)
    assert_that(run.result.stdout).contains(
        f"Partial sweep, resumes next run: {cap} deleted, {12 - cap} remaining"
        f" (cap of {cap} deletions reached)",
    )
    assert_that(run.gh_log.count("--method DELETE")).is_equal_to(cap)
    assert_that(run.result.stderr).does_not_contain("value too great for base")


def test_sweep_ci_ghcr_tags_reads_a_leading_zero_budget_as_decimal(
    tmp_path: Path,
) -> None:
    """``0900`` is a 900-second budget, not invalid octal that disables it.

    Deletes cost 100 fake seconds; new deletions stop at 900 - 300 (reserve).
    """
    run = _run_sweep(
        tmp_path,
        {
            "TIME_BUDGET_SECONDS": "0900",
            "MAX_DELETES": "0",
            "GH_DELETE_COST": "100",
            "GH_VERSIONS": _rows(*_PERSISTENT_ROWS, *_sole_ci_rows(*range(301, 313))),
        },
        clock=True,
    )

    assert_that(run.result.returncode).is_equal_to(0)
    assert_that(run.result.stdout).contains(
        "Partial sweep, resumes next run: 6 deleted, 6 remaining"
        " (time budget of 900s spent)",
    )
    assert_that(run.result.stderr).does_not_contain("value too great for base")


def test_sweep_ci_ghcr_tags_stops_at_the_delete_cap(tmp_path: Path) -> None:
    """At MAX_DELETES the sweep stops, verifies, and exits 0 as partial (#3492)."""
    step_summary = tmp_path / "summary.md"

    run = _run_sweep(
        tmp_path / "run",
        {
            "GH_VERSIONS": _rows(*_PERSISTENT_ROWS, *_sole_ci_rows(301, 302, 303, 304)),
            "MAX_DELETES": "2",
            "GITHUB_STEP_SUMMARY": str(step_summary),
        },
    )

    assert_that(run.result.returncode).is_equal_to(0)
    assert_that(run.result.stdout).contains("Deleted py-lintro version 301")
    assert_that(run.result.stdout).contains("Deleted py-lintro version 302")
    assert_that(run.gh_log).does_not_contain("py-lintro/versions/303")
    assert_that(run.gh_log).does_not_contain("py-lintro/versions/304")
    expected = (
        "Partial sweep, resumes next run: 2 deleted, 2 remaining"
        " (cap of 2 deletions reached)"
    )
    assert_that(run.result.stdout).contains(f"::notice::{expected}")
    assert_that(step_summary.read_text()).contains(f"ci-* sweep: {expected}")
    # What was deleted is still verified.
    assert_that(run.list_calls).is_equal_to(2)
    # Listings ask for the API's largest page so pagination stays short.
    assert_that(run.gh_log).contains("py-lintro/versions?per_page=100&page=1")


def test_sweep_ci_ghcr_tags_zero_disables_both_limits(tmp_path: Path) -> None:
    """``0`` turns the cap and the budget off: a slow backlog is drained whole."""
    run = _run_sweep(
        tmp_path,
        {
            "MAX_DELETES": "0",
            "TIME_BUDGET_SECONDS": "0",
            "GH_DELETE_COST": "5000",
            "GH_VERSIONS": _rows(*_PERSISTENT_ROWS, *_sole_ci_rows(301, 302, 303, 304)),
        },
        clock=True,
    )

    assert_that(run.result.returncode).is_equal_to(0)
    assert_that(run.result.stdout).contains("Sweep complete: 4 deleted")
    assert_that(run.clock).is_equal_to(20000)


def test_sweep_ci_ghcr_tags_dry_run_is_exempt_from_the_cap(tmp_path: Path) -> None:
    """A dry run previews every candidate rather than stopping at MAX_DELETES."""
    run = _run_sweep(
        tmp_path,
        {
            "DRY_RUN": "true",
            "MAX_DELETES": "1",
            "GH_VERSIONS": _rows(*_PERSISTENT_ROWS, *_sole_ci_rows(301, 302, 303, 304)),
        },
    )

    assert_that(run.result.returncode).is_equal_to(0)
    assert_that(run.result.stdout.count("[dry-run] Would delete")).is_equal_to(4)
    assert_that(run.result.stdout).contains("Sweep complete: 4 would delete")
    assert_that(run.gh_log).does_not_contain("--method DELETE")


def test_sweep_ci_ghcr_tags_stops_when_the_time_budget_is_spent(
    tmp_path: Path,
) -> None:
    """A spent budget stops deletions, still verifies, and skips the next package.

    Each DELETE costs 200 fake seconds. With a 720s budget and the 300s
    verification reserve, deletions stop at 420s: three go (at 0, 200 and
    400), one is left, and the second package is never listed.
    """
    run = _run_sweep(
        tmp_path,
        {
            "PACKAGES": "py-lintro py-lintro-base",
            "GH_VERSIONS": _rows(*_PERSISTENT_ROWS, *_sole_ci_rows(301, 302, 303, 304)),
            "TIME_BUDGET_SECONDS": "720",
            "MAX_DELETES": "0",
            "GH_DELETE_COST": "200",
        },
        clock=True,
    )

    assert_that(run.result.returncode).is_equal_to(0)
    assert_that(run.result.stdout).contains("Deleted py-lintro version 303")
    assert_that(run.result.stdout).does_not_contain("Deleted py-lintro version 304")
    assert_that(run.result.stdout).contains(
        "::notice::Partial sweep, resumes next run: 3 deleted, 1 remaining"
        " (time budget of 720s spent); not swept this run: py-lintro-base",
    )
    assert_that(run.result.stdout).contains(
        "::warning::Not swept this run: py-lintro-base",
    )
    assert_that(run.gh_log).does_not_contain("py-lintro-base")
    # The deletions were verified: the listing plus one confirmation.
    assert_that(run.list_calls).is_equal_to(2)
    assert_that(run.result.stderr).does_not_contain("Could not verify")


def test_sweep_ci_ghcr_tags_stops_a_listing_that_overruns_the_budget(
    tmp_path: Path,
) -> None:
    """A listing too slow for the budget stops between pages as a partial run.

    Fifty one-version pages at 100 fake seconds each would take 5,000s against
    a 1,200s budget. New pages stop at 1,200 - 300 = 900s, nothing is deleted
    from the half-read package, and the run exits 0 well inside the job
    timeout instead of being cancelled mid-pagination.
    """
    run = _run_sweep(
        tmp_path,
        {
            "PAGE_SIZE": "1",
            "GH_LIST_COST": "100",
            "GH_VERSIONS": _rows(*_PERSISTENT_ROWS, *_sole_ci_rows(*range(301, 349))),
        },
        clock=True,
    )

    assert_that(run.result.returncode).is_equal_to(0)
    assert_that(run.list_calls).is_equal_to(9)
    assert_that(run.gh_log).does_not_contain("--method DELETE")
    assert_that(run.result.stdout).contains("did not finish within the time budget")
    assert_that(run.result.stdout).contains(
        "::warning::The ci-* sweep made no progress this run",
    )
    assert_that(run.clock).is_less_than_or_equal_to(1200)


def test_sweep_ci_ghcr_tags_keeps_room_to_verify_after_a_slow_listing(
    tmp_path: Path,
) -> None:
    """Deletions stop early enough that the verification listing still fits.

    The listing takes 200s, so the package reserves twice that (400s) of its
    1,200s budget for verification: deletes at 100s each run until 800s,
    and the verification listing then ends at 1,000s — inside the budget.
    """
    run = _run_sweep(
        tmp_path,
        {
            "GH_LIST_COST": "200",
            "GH_DELETE_COST": "100",
            "MAX_DELETES": "0",
            "GH_VERSIONS": _rows(*_PERSISTENT_ROWS, *_sole_ci_rows(*range(301, 321))),
        },
        clock=True,
    )

    assert_that(run.result.returncode).is_equal_to(0)
    assert_that(run.gh_log.count("--method DELETE")).is_equal_to(6)
    assert_that(run.result.stdout).contains("6 deleted, 14 remaining")
    assert_that(run.list_calls).is_equal_to(2)
    assert_that(run.clock).is_equal_to(1000)


def test_sweep_ci_ghcr_tags_bounds_a_verification_that_cannot_finish(
    tmp_path: Path,
) -> None:
    """A verification listing that outruns the budget stops and fails closed.

    The listing is fast but the registry then slows to 60s a page (the
    per-call timeout): verification pages stop starting at the 1,200s budget,
    so the run ends by budget + one call and reports it could not verify.
    """
    run = _run_sweep(
        tmp_path,
        {
            "PAGE_SIZE": "10",
            "GH_LIST_COST": "1",
            "GH_LIST_SLOW_FROM": "10",
            "GH_LIST_SLOW_COST": "60",
            "GH_DELETE_COST": "100",
            "MAX_DELETES": "0",
            "GH_VERSIONS": _rows(
                *_PERSISTENT_ROWS,
                *(_version_row(vid, f"rel-{vid}") for vid in range(1000, 1070)),
                *_sole_ci_rows(*range(301, 310)),
            ),
        },
        clock=True,
    )

    assert_that(run.result.returncode).is_equal_to(1)
    assert_that(run.result.stderr).contains("Could not verify persistent tags")
    assert_that(run.result.stderr).contains("budget ran out")
    assert_that(run.result.stderr).does_not_contain("Persistent tags disappeared")
    assert_that(run.clock).is_less_than_or_equal_to(1200 + 60)


def test_sweep_ci_ghcr_tags_kills_a_hung_gh_call(tmp_path: Path) -> None:
    """A gh call that hangs is killed after GH_CALL_TIMEOUT_SECONDS."""
    run = _run_sweep(tmp_path, {"GH_SLEEP": "30", "GH_CALL_TIMEOUT_SECONDS": "1"})

    assert_that(run.result.returncode).is_equal_to(1)
    assert_that(run.result.stderr).contains("gh call timed out after 1s")


def test_sweep_ci_ghcr_tags_rotation_stops_a_slow_package_starving_the_next(
    tmp_path: Path,
) -> None:
    """Rotating the start package lets a later package drain (#3492).

    py-lintro's listing takes 1,000 fake seconds, so a run that starts with it
    leaves no time for anything else. The next run (the next run number)
    starts with py-lintro-base instead and deletes its backlog.
    """
    env = {
        "PACKAGES": "py-lintro py-lintro-base",
        "GH_LIST_COSTS": "py-lintro=1000 py-lintro-base=1",
        "GH_VERSIONS": _rows(*_PERSISTENT_ROWS, *_sole_ci_rows(301, 302)),
    }

    first = _run_sweep(tmp_path / "run1", {**env, "PACKAGE_ROTATION": "0"}, clock=True)
    second = _run_sweep(
        tmp_path / "run2",
        {**env, "PACKAGE_ROTATION": "1"},
        clock=True,
    )

    assert_that(first.result.returncode).is_equal_to(0)
    assert_that(first.result.stdout).contains(
        "Package order this run: py-lintro py-lintro-base",
    )
    assert_that(first.gh_log).does_not_contain("--method DELETE")
    assert_that(first.result.stdout).contains(
        "::warning::The ci-* sweep made no progress this run",
    )
    assert_that(first.result.stdout).contains(
        "::warning::Not swept this run: py-lintro-base",
    )
    assert_that(second.result.returncode).is_equal_to(0)
    assert_that(second.result.stdout).contains(
        "Package order this run: py-lintro-base py-lintro",
    )
    assert_that(second.result.stdout).contains("Deleted py-lintro-base version 301")
    assert_that(second.result.stdout).contains("Deleted py-lintro-base version 302")


#: Environment of a GitHub Actions job on a GitHub-hosted runner.
_HOSTED_RUNNER_ENV = {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted"}


def _free_disk_stubs(bin_dir: Path, log: Path, *, sudo_exit: int = 0) -> None:
    """Shadow ``sudo`` and ``df`` so free-disk-space.sh runs without root.

    Args:
        bin_dir: Directory prepended to PATH for the script under test.
        log: File each stub appends its argv to.
        sudo_exit: Exit status the ``sudo`` stub returns.
    """
    _write_stub(bin_dir, "sudo", f'echo "sudo $*" >> "{log}"\nexit {sudo_exit}')
    _write_stub(bin_dir, "df", f'echo "df $*" >> "{log}"')


def test_free_disk_space_reports_disk_and_spares_job_toolcache(
    tmp_path: Path,
) -> None:
    """The cleanup reports df around itself and never touches used toolcaches.

    Runs before every hosted docker-ci image build (#3085), so it must keep
    the Python/Node toolcache entries other steps may resolve and must stay
    re-runnable.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    _free_disk_stubs(bin_dir, log)

    for _ in range(2):
        result = _run_with_stubs(
            "scripts/ci/free-disk-space.sh",
            bin_dir,
            _HOSTED_RUNNER_ENV,
        )
        assert_that(result.returncode).described_as(result.stderr).is_equal_to(0)

    calls = log.read_text().splitlines()
    assert_that(calls.count("df -h /")).is_equal_to(4)
    rm_targets = [
        target
        for call in calls
        if call.startswith("sudo rm -rf ")
        for target in call.removeprefix("sudo rm -rf ").split()
    ]
    assert_that(rm_targets).contains("/opt/hostedtoolcache/CodeQL")
    assert_that(rm_targets).does_not_contain("/opt/hostedtoolcache")
    assert_that(
        [t for t in rm_targets if re.search(r"hostedtoolcache/(Python|node)", t)],
    ).is_empty()


def test_free_disk_space_fails_when_cleanup_fails(tmp_path: Path) -> None:
    """A failing privileged cleanup fails the step instead of being masked."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    _free_disk_stubs(bin_dir, log, sudo_exit=1)

    result = _run_with_stubs(
        "scripts/ci/free-disk-space.sh",
        bin_dir,
        _HOSTED_RUNNER_ENV,
    )

    assert_that(result.returncode).is_not_equal_to(0)
    calls = log.read_text().splitlines()
    # The script stops at the failing ``sudo rm``: one sudo call, and only
    # the "before" df ran.
    assert_that([call for call in calls if call.startswith("sudo ")]).is_length(1)
    assert_that(calls.count("df -h /")).is_equal_to(1)


@pytest.mark.parametrize(
    ("runner_environment", "annotation"),
    [("self-hosted", "::notice::"), ("", "::warning::")],
    ids=["self_hosted", "empty"],
)
def test_free_disk_space_skips_off_github_hosted_runners(
    runner_environment: str,
    annotation: str,
    tmp_path: Path,
) -> None:
    """Off a GitHub-hosted runner the cleanup is a no-op with an annotation.

    A self-hosted runner's toolchains belong to the host, so the script must
    not run ``sudo rm`` there even inside GitHub Actions. That skip is a
    notice; an empty ``RUNNER_ENVIRONMENT``, which Actions always sets, is a
    warning because a hosted runner's cleanup may be skipped by mistake.

    Args:
        runner_environment: ``RUNNER_ENVIRONMENT`` value for the run.
        annotation: Workflow command the skip message must start with.
        tmp_path: Temporary directory for stubs and the call log.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    _free_disk_stubs(bin_dir, log)

    result = _run_with_stubs(
        "scripts/ci/free-disk-space.sh",
        bin_dir,
        {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": runner_environment},
    )

    assert_that(result.returncode).described_as(result.stderr).is_equal_to(0)
    assert_that(result.stdout).starts_with(f"{annotation}Skipping disk cleanup")
    assert_that(result.stdout).contains("not github-hosted")
    assert_that(log.exists()).is_false()
