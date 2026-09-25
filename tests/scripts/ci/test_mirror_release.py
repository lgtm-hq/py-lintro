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
import yaml
from assertpy import assert_that

ROOT = Path(__file__).resolve().parents[3]
MIRROR_DIR = ROOT / "scripts" / "ci" / "mirror"
RESOLVE_SCRIPT = MIRROR_DIR / "resolve-version.sh"
BUMP_SCRIPT = MIRROR_DIR / "bump_pin.py"
WAIT_WHEEL_SCRIPT = MIRROR_DIR / "wait-for-pypi-wheel.sh"
PUBLISH_SCRIPT = MIRROR_DIR / "publish-mirror-release.sh"
CLASSIFY_SCRIPT = ROOT / "scripts" / "ci" / "classify-release-tag.py"
MIRROR_WORKFLOW = ROOT / ".github" / "workflows" / "mirror-release.yml"


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
    lgtm-ci's shared create-signed-commit script (createCommitOnBranch) for
    the commit (#2834), `--auto` plus a bounded MERGED poll for the merge.
    """
    body = PUBLISH_SCRIPT.read_text(encoding="utf-8")

    # Signed, attributed commit: created by the shared lgtm-ci script in
    # reset mode, never by an inline mutation or a local git commit.
    assert_that(body).contains(
        "${LGTM_CI_TOOLING_DIR}/scripts/ci/git/create-signed-commit.sh",
    )
    assert_that(body).contains('bash "$SIGNED_COMMIT_SCRIPT"')
    assert_that(body).contains("--mode reset")
    assert_that(body).contains('--base "$base_oid"')
    assert_that(body).contains('--branch "$BRANCH"')
    assert_that(body).contains('--repository "$MIRROR_REPO"')
    assert_that(body).contains("--file pyproject.toml")
    assert_that(body).contains("commit-sha=")
    assert_that(body).does_not_contain("gh api graphql")
    assert_that(body).does_not_contain("expectedHeadOid")
    assert_that(body).does_not_contain("git commit")
    # Merge: the repo setting is checked, auto-merge armed, then a bounded
    # poll — never an immediate merge.
    assert_that(body).contains("allow_auto_merge")
    assert_that(body).contains("--squash --auto")
    assert_that(body).contains("MERGE_TIMEOUT_SECONDS")
    assert_that(body).contains("MERGE_POLL_SECONDS")
    assert_that(body).contains('== "MERGED"')
    # Closed PRs stop the poll early; timeout prints the PR URL.
    assert_that(body).contains('== "CLOSED"')
    assert_that(body).contains("pull/${pr_number}")


def test_publish_script_requires_the_lgtm_ci_tooling_checkout(tmp_path: Path) -> None:
    """A missing LGTM_CI_TOOLING_DIR fails clearly before any mirror write.

    Runs the real script with stub git/gh on PATH: the pin changes, so a
    bump commit is needed, and the run must stop naming the variable before
    the auto-merge check or any branch, commit or PR call.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh_log = tmp_path / "gh.log"
    for name, script in (
        ("git", '#!/usr/bin/env bash\n[[ "$1" == "diff" ]] && exit 1\nexit 0\n'),
        ("gh", f'#!/usr/bin/env bash\necho "$*" >>"{gh_log}"\nexit 0\n'),
    ):
        stub = bin_dir / name
        stub.write_text(script, encoding="utf-8")
        stub.chmod(0o755)
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    (mirror / "pyproject.toml").write_text(
        '[project]\ndependencies = ["lintro==1.2.2"]\n',
        encoding="utf-8",
    )

    for tooling_dir, message in (
        (None, "LGTM_CI_TOOLING_DIR is not set"),
        (str(tmp_path / "no-such-dir"), "create-signed-commit script not found"),
    ):
        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
        env["GH_TOKEN"] = "stub"  # nosec B105 - placeholder for the stub gh
        env["MIRROR_DIR"] = str(mirror)
        env.pop("LGTM_CI_TOOLING_DIR", None)
        if tooling_dir is not None:
            env["LGTM_CI_TOOLING_DIR"] = tooling_dir
        result = subprocess.run(  # nosec B603 - fixed argv against repo script
            [str(PUBLISH_SCRIPT), "1.2.3"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
            cwd=ROOT,
        )

        assert_that(result.returncode).is_not_equal_to(0)
        assert_that(result.stderr + result.stdout).contains(message)
        # Nothing reached the mirror: not even the auto-merge setting read.
        assert_that(gh_log.exists()).is_false()


def test_publish_script_heals_a_stale_bump_branch() -> None:
    """A stale bump branch is reset or recreated, never rebased (#2742).

    The rerun path reused a leftover branch and rebased it into the same
    conflict every time (lintro-pre-commit #23 sat dirty until merged by
    hand). Rebuilding from the mirror's current main cannot conflict. An
    unmergeable open PR's branch is deleted (closing that PR) before the
    reset; a branch with no open PR is moved in place by reset mode, so the
    script no longer POSTs the ref itself (#2834).
    """
    body = PUBLISH_SCRIPT.read_text(encoding="utf-8")

    assert_that(body).contains("git ls-remote --heads origin")
    assert_that(body).contains(
        'gh api -X DELETE "repos/${MIRROR_REPO}/git/refs/heads/${BRANCH}"',
    )
    assert_that(body).does_not_contain('gh api -X POST "repos/${MIRROR_REPO}/git/refs"')
    assert_that(body).does_not_contain("git rebase")


def test_mirror_workflow_checks_out_lgtm_ci_tooling_for_the_publish_step() -> None:
    """The mirror-bump job provides the shared signed-commit script (#2834).

    The lgtm-ci checkout must be sparse, credential-free, pinned to a full
    SHA, placed before the publish step, and exposed to it as an absolute
    LGTM_CI_TOOLING_DIR.
    """
    workflow = yaml.safe_load(MIRROR_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["mirror-bump"]["steps"]
    names = [step.get("name") for step in steps]

    checkout_index = names.index("Checkout lgtm-ci tooling")
    publish_index = names.index("Publish mirror release")
    assert_that(checkout_index).is_less_than(publish_index)

    checkout = steps[checkout_index]
    assert_that(checkout["uses"]).starts_with("actions/checkout@")
    assert_that(checkout["if"]).is_equal_to(steps[publish_index]["if"])
    with_ = checkout["with"]
    assert_that(with_["repository"]).is_equal_to("lgtm-hq/lgtm-ci")
    assert_that(with_["path"]).is_equal_to(".lgtm-ci-tooling")
    assert_that(with_["sparse-checkout"]).is_equal_to("scripts/ci/")
    assert_that(with_["persist-credentials"]).is_false()
    assert_that(with_["ref"]).matches(r"^[0-9a-f]{40}$")

    publish_env = steps[publish_index]["env"]
    assert_that(publish_env["LGTM_CI_TOOLING_DIR"]).is_equal_to(
        "${{ github.workspace }}/.lgtm-ci-tooling",
    )

    # Harden-runner must let the checkout reach GitHub.
    harden = steps[0]
    assert_that(harden["uses"]).starts_with("step-security/harden-runner@")
    endpoints = harden["with"]["allowed-endpoints"].split()
    assert_that(endpoints).contains("github.com:443", "codeload.github.com:443")


def test_publish_script_reuses_a_healthy_open_pr_before_healing() -> None:
    """A mergeable open PR is reused; only unmergeable branches are healed.

    An earlier run's auto-merge can still be pending; deleting the branch
    would close a healthy PR and restart its checks from zero. The script
    asks for the PR's state first and only heals a dirty/abandoned branch.
    Reuse is gated on the PR targeting main (merging a PR against another
    base would tag a main that never received the bump).
    """
    body = PUBLISH_SCRIPT.read_text(encoding="utf-8")

    assert_that(body).contains("gh pr list --head")
    assert_that(body).contains("--base main")
    assert_that(body).contains("mergeStateStatus")
    assert_that(body).contains("baseRefName")
    assert_that(body).contains("Reusing open PR")
    assert_that(body).contains("healing the branch")


def test_publish_script_opens_exactly_one_pr_and_checks_setting_first() -> None:
    """The fresh path creates one PR and the setting check precedes writes.

    Head 8afbdd2f shipped two P1s this pins: a duplicated `gh pr create`
    block (the second call dies under set -e — "a pull request for branch
    ... already exists"), and the allow_auto_merge precondition buried in
    the merge step, after the ref POST, the signed commit and the PR.
    """
    body = PUBLISH_SCRIPT.read_text(encoding="utf-8")

    assert_that(body.count("gh pr create")).is_equal_to(1)
    # The precondition call must precede every mirror write in the main
    # flow (anchored at the sync block, after the function definitions).
    lines = body.splitlines()
    main_flow = next(
        i for i, line in enumerate(lines) if "Sync to the mirror's current main" in line
    )
    setting_line = next(
        i
        for i, line in enumerate(lines)
        if line.strip() == "require_auto_merge_enabled"
    )
    assert_that(setting_line).is_greater_than(main_flow)
    for write in (
        "git/refs/heads/${BRANCH}",  # heal DELETE
        'bash "$SIGNED_COMMIT_SCRIPT"',  # shared create-signed-commit call
        "gh pr create",  # fresh PR
        "pr merge",  # --auto
    ):
        for i, line in enumerate(lines):
            if write in line and main_flow < i < setting_line:
                pytest.fail(
                    f"mirror write {write!r} at line {i + 1} precedes the precondition",
                )


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


def _wheel_metadata_extra(digest: str, extra_name: str) -> str:
    """PyPI JSON with the manifest wheel plus an extra same-version wheel.

    The extra entry is *unmanifested*: not in the release SHA256SUMS. Both
    wheels must clear the per-wheel digest + attestation loop before the
    set-equality check rejects the extra one.
    """
    import json

    base = json.loads(_wheel_metadata(digest))
    base["urls"].append(
        {
            "packagetype": "bdist_wheel",
            "filename": extra_name,
            "url": f"https://files.pythonhosted.org/packages/bb/{extra_name}",
            "digests": {"sha256": digest},
        },
    )
    return json.dumps(base)


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


def test_verify_wheel_rejects_extra_pypi_wheel_not_in_manifest(tmp_path: Path) -> None:
    """An extra same-version wheel on PyPI fails the gate (set equality).

    Verifying only the first bdist_wheel would let pip/pre-commit install an
    unmanifested wheel while the gate passes on the manifest-listed one; the
    PyPI wheel set must equal the manifest wheel set.
    """
    extra = "lintro-1.2.3-cp312-cp312-macosx_11_0_arm64.whl"
    env = _setup_wheel_env(
        tmp_path,
        metadata=_wheel_metadata_extra(WHEEL_SHA, extra),
        manifest=f"{WHEEL_SHA}  {WHEEL_NAME}\n",
    )

    result = _run_verify_wheel(env)

    assert_that(result.returncode).is_not_equal_to(0)
    # The per-wheel loop refuses the unmanifested wheel first ("has no entry
    # for"); the set-equality check is the backstop — either guard must fire.
    combined = result.stderr + result.stdout
    assert_that(combined).contains("has no entry for")


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
