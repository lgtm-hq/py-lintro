# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Tests for lintro-pre-commit mirror release automation scripts."""

from __future__ import annotations

import importlib.util
import os
import subprocess  # nosec B404 - drives repo shell scripts with shell=False
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

ROOT = Path(__file__).resolve().parents[3]
MIRROR_DIR = ROOT / "scripts" / "ci" / "mirror"
RESOLVE_SCRIPT = MIRROR_DIR / "resolve-version.sh"
BUMP_SCRIPT = MIRROR_DIR / "bump_pin.py"
WAIT_WHEEL_SCRIPT = MIRROR_DIR / "wait-for-pypi-wheel.sh"
PUBLISH_SCRIPT = MIRROR_DIR / "publish-mirror-release.sh"
CLASSIFY_SCRIPT = ROOT / "scripts" / "ci" / "classify-release-tag.py"


def _load_bump_pin_module() -> Any:
    """Load bump_pin.py as an importable module."""
    spec = importlib.util.spec_from_file_location("bump_pin", BUMP_SCRIPT)
    assert_that(spec).is_not_none()
    assert spec is not None
    assert_that(spec.loader).is_not_none()
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_resolve(
    *,
    release_tag: str,
    github_output: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run resolve-version.sh with the given release tag."""
    env = os.environ.copy()
    env["RELEASE_TAG"] = release_tag
    if github_output is not None:
        env["GITHUB_OUTPUT"] = str(github_output)
    else:
        env.pop("GITHUB_OUTPUT", None)
    return subprocess.run(  # nosec B603 - fixed argv against repo script; shell=False
        [str(RESOLVE_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=ROOT,
    )


@pytest.mark.parametrize(
    ("tag", "expected_prerelease"),
    [
        ("v1.2.3", "false"),
        ("1.2.3", "false"),
        ("v1.2.3+build.1", "false"),
        ("v1.2.3rc1", "true"),
        ("v1.2.3-rc.1", "true"),
        ("v1.2.3-alpha.1", "true"),
        ("v1.2.3.dev1", "true"),
        ("v1.2.3RC1", "true"),
        ("garbage", "true"),
    ],
)
def test_resolve_version_matches_classifier(
    tag: str,
    expected_prerelease: str,
    tmp_path: Path,
) -> None:
    """resolve-version.sh classifies tags like classify-release-tag.py."""
    output_file = tmp_path / "gh_output"
    result = _run_resolve(release_tag=tag, github_output=output_file)

    assert_that(result.returncode).is_equal_to(0)
    body = output_file.read_text(encoding="utf-8")
    assert_that(body).contains(f"is_prerelease={expected_prerelease}")
    assert_that(body).contains(f"version={tag.lstrip('v')}")


def test_resolve_version_writes_tag_and_version(
    tmp_path: Path,
) -> None:
    """resolve-version.sh emits tag and version outputs."""
    output_file = tmp_path / "gh_output"
    result = _run_resolve(release_tag="v0.69.0", github_output=output_file)

    assert_that(result.returncode).is_equal_to(0)
    body = output_file.read_text(encoding="utf-8")
    assert_that(body).contains("tag=v0.69.0")
    assert_that(body).contains("version=0.69.0")
    assert_that(body).contains("is_prerelease=false")


def test_bump_updates_real_dependency_not_decoy_comment(tmp_path: Path) -> None:
    """bump_pin.py rewrites the parsed dependency, not an earlier decoy string."""
    module = _load_bump_pin_module()
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        textwrap.dedent(
            """\
            # Example only: "lintro==0.1.0"
            [project]
            name = "lintro-pre-commit"
            dependencies = [
              "lintro==0.69.0",
            ]
            """,
        ),
        encoding="utf-8",
    )

    changed = module.bump(path=pyproject, version="0.70.0")

    assert_that(changed).is_true()
    updated = pyproject.read_text(encoding="utf-8")
    assert_that(updated).contains('"lintro==0.70.0"')
    assert_that(updated).contains('# Example only: "lintro==0.1.0"')


def test_bump_check_reports_drift(tmp_path: Path) -> None:
    """--check exits non-zero when the parsed pin does not match."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        textwrap.dedent(
            """\
            [project]
            dependencies = ["lintro==0.69.0"]
            """,
        ),
        encoding="utf-8",
    )

    ok = subprocess.run(  # nosec B603 - fixed argv; shell=False
        [
            sys.executable,
            str(BUMP_SCRIPT),
            "--pyproject",
            str(pyproject),
            "--version",
            "0.69.0",
            "--check",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    drift = subprocess.run(  # nosec B603 - fixed argv; shell=False
        [
            sys.executable,
            str(BUMP_SCRIPT),
            "--pyproject",
            str(pyproject),
            "--version",
            "0.70.0",
            "--check",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert_that(ok.returncode).is_equal_to(0)
    assert_that(drift.returncode).is_equal_to(1)
    assert_that(drift.stderr).contains("Drift")


def test_bump_missing_pin_raises(tmp_path: Path) -> None:
    """Missing lintro dependency raises a clear error."""
    module = _load_bump_pin_module()
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        textwrap.dedent(
            """\
            [project]
            dependencies = ["other==1.0.0"]
            """,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="No 'lintro==<version>' pin"):
        module.bump(path=pyproject, version="1.0.0")


def test_bump_multiple_pins_raises(tmp_path: Path) -> None:
    """Multiple lintro pins in dependency tables fail closed."""
    module = _load_bump_pin_module()
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        textwrap.dedent(
            """\
            [project]
            dependencies = ["lintro==0.69.0"]
            optional-dependencies.dev = ["lintro==0.68.0"]
            """,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Expected exactly one"):
        module.bump(path=pyproject, version="1.0.0")


def test_resolve_version_rejects_empty_tag() -> None:
    """Whitespace-only RELEASE_TAG fails closed instead of publishing."""
    result = _run_resolve(release_tag="   ")

    assert_that(result.returncode).is_not_equal_to(0)


def test_publish_script_creates_commit_via_the_api_and_never_races_merge() -> None:
    """The bump commit is API-created and the merge waits for checks (#2742).

    Plain `git commit` under a noreply identity produced an unsigned,
    unattributed commit the mirror's rulesets reject, and an immediate
    `gh pr merge` raced the required checks. Pins the replacement shape:
    createCommitOnBranch for the commit, `--auto` plus a bounded MERGED poll
    for the merge.
    """
    body = PUBLISH_SCRIPT.read_text(encoding="utf-8")

    # Signed, attributed commit: created through the GraphQL API.
    assert_that(body).contains("createCommitOnBranch")
    assert_that(body).contains("expectedHeadOid")
    assert_that(body).does_not_contain("git commit")
    # Merge: auto-merge, then a bounded poll, never an immediate merge.
    assert_that(body).contains("--squash --delete-branch --auto")
    assert_that(body).contains("MERGE_TIMEOUT_SECONDS")
    assert_that(body).contains("MERGE_POLL_SECONDS")
    assert_that(body).contains('== "MERGED"')
    # Timeout prints the PR URL for a human to finish.
    assert_that(body).contains("pull/${pr_number}")


def test_publish_script_heals_a_stale_bump_branch() -> None:
    """An existing bump branch is deleted and recreated, not rebased (#2742).

    The rerun path reused a leftover branch and rebased it into the same
    conflict every time (lintro-pre-commit #23 sat dirty until merged by
    hand). Recreation from the mirror's current main cannot conflict.
    createCommitOnBranch appends to an existing branch, so the ref is also
    (re)created at the base oid before the mutation runs.
    """
    body = PUBLISH_SCRIPT.read_text(encoding="utf-8")

    assert_that(body).contains("git ls-remote --heads origin")
    assert_that(body).contains("git/refs/heads/${BRANCH}")
    # The ref must exist before the commit mutation (fresh + heal runs).
    assert_that(body).contains('gh api -X POST "repos/${MIRROR_REPO}/git/refs"')
    assert_that(body).does_not_contain("git rebase")


def _write_fake_curl(bin_dir: Path, payload: str) -> None:
    """Install a curl stub that prints *payload* and ignores URL/flags."""
    curl = bin_dir / "curl"
    curl.write_text(
        f"#!/usr/bin/env bash\ncat <<'EOF'\n{payload}\nEOF\n",
        encoding="utf-8",
    )
    curl.chmod(0o755)


def _run_wait_wheel(
    *,
    bin_dir: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run wait-for-pypi-wheel.sh with a stub curl ahead of PATH."""
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(  # nosec B603 - fixed argv against repo script; shell=False
        [str(WAIT_WHEEL_SCRIPT), "lintro", "1.2.3", "1", "0"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=ROOT,
    )


def test_wait_for_pypi_wheel_requires_bdist_wheel(tmp_path: Path) -> None:
    """sdist-only PyPI metadata is not enough to pass the wheel gate."""
    _write_fake_curl(tmp_path, '{"urls":[{"packagetype":"sdist"}]}')
    result = _run_wait_wheel(bin_dir=tmp_path)

    assert_that(result.returncode).is_equal_to(1)
    assert_that(result.stderr + result.stdout).contains("Timeout")


def test_wait_for_pypi_wheel_accepts_bdist_wheel(tmp_path: Path) -> None:
    """A bdist_wheel URL in the PyPI JSON is sufficient."""
    _write_fake_curl(tmp_path, '{"urls":[{"packagetype":"bdist_wheel"}]}')
    result = _run_wait_wheel(bin_dir=tmp_path)

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stderr + result.stdout).contains("wheel is available")


def test_wait_for_pypi_wheel_times_out_without_metadata(tmp_path: Path) -> None:
    """Empty curl output (metadata not published yet) exits 1 after attempts."""
    curl = tmp_path / "curl"
    curl.write_text("#!/usr/bin/env bash\nexit 22\n", encoding="utf-8")
    curl.chmod(0o755)
    result = _run_wait_wheel(bin_dir=tmp_path)

    assert_that(result.returncode).is_equal_to(1)
    assert_that(result.stderr + result.stdout).contains("Timeout")


# --- verify_release_wheel.sh (#2742) -----------------------------------------

VERIFY_WHEEL_SCRIPT = MIRROR_DIR / "verify_release_wheel.sh"
WHEEL_NAME = "lintro-1.2.3-py3-none-any.whl"
WHEEL_SHA = "e0e77a507412b120f6ede61f62295b1a7b2ff19d3dcc8f7253e51663470c888e"


def _wheel_metadata(digest: str) -> str:
    """Return a minimal PyPI JSON payload with one wheel at *digest*."""
    import json

    return json.dumps(
        {
            "urls": [
                {
                    "packagetype": "bdist_wheel",
                    "filename": WHEEL_NAME,
                    "url": f"https://files.pythonhosted.org/packages/aa/{WHEEL_NAME}",
                    "digests": {"sha256": digest},
                },
            ],
        },
    )


def _setup_wheel_env(
    tmp_path: Path,
    *,
    metadata: str,
    manifest: str,
    attestation_ok: bool = True,
    gh_fail: bool = False,
    wheel_bytes: bytes | None = None,
) -> dict[str, str]:
    """Stub gh and curl, write a SHA256SUMS, return the env for the script.

    The stubbed `gh release download` materializes SHA256SUMS and the
    stubbed curl serves the PyPI JSON for the metadata URL and *wheel_bytes*
    (default: 32 bytes of 0xAA, sha256 == WHEEL_SHA) for the files
    .pythonhosted.org wheel URL, so the download-digest check passes and only
    the intended failure mode (attestation, mismatch, unlisted) fires.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh_log = tmp_path / "gh.log"

    attestation_rc = "0" if attestation_ok else "1"
    download_rc = "1" if gh_fail else "0"
    work_dir = tmp_path / "work"
    manifest_path = work_dir / "SHA256SUMS"
    # The curl stub copies this file as the wheel body so the manifest digest
    # check sees the exact attested bytes (32 bytes of 0xAA) by default.
    wheel_source = tmp_path / "wheel-source.bin"
    wheel_source.write_bytes(
        wheel_bytes if wheel_bytes is not None else bytes([0xAA]) * 32,
    )
    (bin_dir / "gh").write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            echo "$*" >>"{gh_log}"
            if [[ "$1" == "attestation" ]]; then
                exit {attestation_rc}
            fi
            if [[ "$*" == *"--pattern SHA256SUMS"* ]]; then
                mkdir -p "{work_dir}"
                printf '%s\\n' "{manifest}" >"{manifest_path}"
                exit {download_rc}
            fi
            exit 0
            """,
        ),
        encoding="utf-8",
    )
    (bin_dir / "gh").chmod(0o755)
    (bin_dir / "curl").write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            out=/dev/stdout
            for arg in "$@"; do
                if [[ "$arg" == *files.pythonhosted.org* ]]; then
                    wheel_seen=1
                fi
            done
            # emulate curl -o: the previous -o argument takes the body
            argv=("$@")
            if [[ "${{wheel_seen:-0}}" == "1" ]]; then
                for i in "${{!argv[@]}}"; do
                    if [[ "${{argv[$i]}}" == "-o" ]]; then
                        out="${{argv[$((i + 1))]}}"
                    fi
                done
                cat "{wheel_source}" >"$out"
                exit 0
            fi
            cat <<'EOF'
            {metadata}
            EOF
            """,
        ),
        encoding="utf-8",
    )
    (bin_dir / "curl").chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["VERSION"] = "1.2.3"
    env["ATTESTATION_REPO"] = "lgtm-hq/py-lintro"
    env["DIST_SIGNER_WORKFLOW"] = "lgtm-hq/lgtm-ci/.github/workflows/x.yml"
    env["WORK_DIR"] = str(tmp_path / "work")
    return env


def _run_verify_wheel(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run verify_release_wheel.sh with the prepared environment."""
    return subprocess.run(  # nosec B603 - fixed argv against repo script; shell=False
        [str(VERIFY_WHEEL_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=ROOT,
    )


def test_verify_wheel_matches_manifest_and_attestation(tmp_path: Path) -> None:
    """PyPI digest == manifest digest + passing attestation is the only green."""
    metadata = _wheel_metadata(WHEEL_SHA)
    env = _setup_wheel_env(
        tmp_path,
        metadata=metadata,
        manifest=f"{WHEEL_SHA}  {WHEEL_NAME}\n",
    )

    result = _run_verify_wheel(env)

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("sha256 ok")
    assert_that(result.stdout).contains("attestation ok")


def test_verify_wheel_rejects_digest_mismatch(tmp_path: Path) -> None:
    """A PyPI wheel whose bytes differ from the release manifest fails closed."""
    metadata = _wheel_metadata("b" * 64)
    env = _setup_wheel_env(
        tmp_path,
        metadata=metadata,
        manifest=f"{WHEEL_SHA}  {WHEEL_NAME}\n",
    )

    result = _run_verify_wheel(env)

    assert_that(result.returncode).is_not_equal_to(0)
    assert_that(result.stderr).contains("sha256 mismatch")


def test_verify_wheel_rejects_unlisted_wheel(tmp_path: Path) -> None:
    """A wheel the release manifest does not list is refused."""
    metadata = _wheel_metadata(WHEEL_SHA)
    env = _setup_wheel_env(
        tmp_path,
        metadata=metadata,
        manifest=f"{'c' * 64}  some-other-file.whl\n",
    )

    result = _run_verify_wheel(env)

    assert_that(result.returncode).is_not_equal_to(0)
    assert_that(result.stderr).contains("has no entry for")


def test_verify_wheel_rejects_failed_attestation(tmp_path: Path) -> None:
    """A wheel without a passing build attestation fails before any pin."""
    metadata = _wheel_metadata(WHEEL_SHA)
    env = _setup_wheel_env(
        tmp_path,
        metadata=metadata,
        manifest=f"{WHEEL_SHA}  {WHEEL_NAME}\n",
        attestation_ok=False,
    )

    result = _run_verify_wheel(env)

    assert_that(result.returncode).is_not_equal_to(0)
    assert_that(result.stderr).contains("attestation verification failed")


def test_verify_wheel_rejects_tampered_downloaded_bytes(tmp_path: Path) -> None:
    """Bytes fetched from the PyPI URL must hash to the manifest digest.

    The metadata digest can be read from PyPI's JSON without fetching the
    file; this test pins the second guard: the bytes curl downloads from the
    wheel URL are hashed locally and must equal the manifest entry.
    """
    metadata = _wheel_metadata(WHEEL_SHA)
    env = _setup_wheel_env(
        tmp_path,
        metadata=metadata,
        manifest=f"{WHEEL_SHA}  {WHEEL_NAME}\n",
        wheel_bytes=bytes([0xBB]) * 32,
    )

    result = _run_verify_wheel(env)

    assert_that(result.returncode).is_not_equal_to(0)
    assert_that(result.stderr).contains("sha256 mismatch for the downloaded")


@pytest.mark.parametrize("missing_var", ["ATTESTATION_REPO", "DIST_SIGNER_WORKFLOW"])
def test_verify_wheel_requires_configuration(
    tmp_path: Path,
    missing_var: str,
) -> None:
    """Missing ATTESTATION_REPO or DIST_SIGNER_WORKFLOW fails closed."""
    env = _setup_wheel_env(
        tmp_path,
        metadata=_wheel_metadata(WHEEL_SHA),
        manifest=f"{WHEEL_SHA}  {WHEEL_NAME}\n",
    )
    env.pop(missing_var)

    result = _run_verify_wheel(env)

    assert_that(result.returncode).is_equal_to(2)
    assert_that(result.stderr).contains(f"{missing_var} is required")
