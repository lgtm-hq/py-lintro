# SPDX-License-Identifier: MIT
"""Behavior and security contracts for Renovate semgrep lock regeneration."""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 - fixed argv invokes repository scripts under test
from pathlib import Path
from typing import Any

import pytest
import yaml
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def lock_repo(tmp_path: Path) -> Path:
    """Copy the scripts into an isolated repository with deterministic tools."""
    scripts = tmp_path / "scripts/ci"
    scripts.mkdir(parents=True)
    for name in (
        "compile-semgrep-lock.sh",
        "semgrep-lock-lib.sh",
        "read-tools-uv-version.sh",
    ):
        shutil.copy2(_REPO_ROOT / "scripts/ci" / name, scripts / name)
    (tmp_path / "requirements-semgrep.in").write_text("semgrep==1.0.0\n")
    (tmp_path / "requirements-semgrep.txt").write_text("original\n")
    (tmp_path / "baseline").write_text("original\n")
    (tmp_path / "docker").mkdir()
    (tmp_path / "docker/tools.Dockerfile").write_text("ARG UV_VERSION=0.12.19\n")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    uv = binaries / "uv"
    uv.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        'printf "%s\\n" "$@" > args\n'
        'if [[ "${FAIL_COMPILE:-0}" == 1 ]]; then exit 42; fi\n'
        'while [[ "$1" != --output-file ]]; do shift; done\n'
        'printf "%s\\n" "${LOCK_CONTENT:-original}" > "$2"\n',
    )
    git = binaries / "git"
    git.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        'if [[ "${FAIL_DIFF:-0}" == 1 ]]; then exit 128; fi\n'
        'cd "$2"\n'
        "cmp -s baseline requirements-semgrep.txt\n",
    )
    for binary in (uv, git):
        binary.chmod(0o755)
    return tmp_path


def _run(
    *,
    root: Path,
    args: tuple[str, ...] = (),
    env: dict[str, str] | None = None,
    script: str = "compile-semgrep-lock.sh",
) -> subprocess.CompletedProcess[str]:
    """Run a copied script from outside its repository with controlled tools."""
    bash = shutil.which("bash")
    if bash is None:
        raise RuntimeError("bash is required to test the CI scripts")
    return subprocess.run(  # nosec B603 - fixed repository script argv, no shell
        [str(Path(bash).resolve()), str(root / "scripts/ci" / script), *args],
        cwd=root.parent,
        env={
            **os.environ,
            "PATH": f"{root / 'bin'}:{os.environ['PATH']}",
            "GITHUB_OUTPUT": str(root / "outputs"),
            **(env or {}),
        },
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("upgrade", [False, True], ids=["preserve-pins", "upgrade"])
@pytest.mark.parametrize("changed", [False, True], ids=["no-op", "changed"])
def test_compile_modes_and_change_output(
    lock_repo: Path,
    upgrade: bool,
    changed: bool,
) -> None:
    """Both resolver modes preserve the shared hash/floor contract and detect edits."""
    content = "updated" if changed else "original"
    result = _run(
        root=lock_repo,
        args=("--upgrade",) if upgrade else (),
        env={"LOCK_CONTENT": content},
    )
    assert_that(result.returncode).described_as(result.stderr).is_equal_to(0)
    assert_that((lock_repo / "requirements-semgrep.txt").read_text()).is_equal_to(
        f"{content}\n",
    )
    assert_that((lock_repo / "outputs").read_text()).is_equal_to(
        f"changed={str(changed).lower()}\n",
    )
    args = (lock_repo / "args").read_text().splitlines()
    expected = [
        "pip",
        "compile",
        "--no-config",
        "--generate-hashes",
        "--python-version",
        "3.11",
        "--output-file",
        "requirements-semgrep.txt",
    ]
    if upgrade:
        expected.append("--upgrade")
    assert_that(args).is_equal_to([*expected, "requirements-semgrep.in"])


@pytest.mark.parametrize(
    ("env", "code"),
    [({"FAIL_COMPILE": "1"}, 42), ({"FAIL_DIFF": "1"}, 128)],
    ids=["resolver-failure", "diff-failure"],
)
def test_failures_never_report_a_successful_change(
    lock_repo: Path,
    env: dict[str, str],
    code: int,
) -> None:
    """Resolver and diff failures stop before advertising an output to the workflow."""
    result = _run(root=lock_repo, env=env)
    assert_that(result.returncode).is_equal_to(code)
    assert_that((lock_repo / "outputs").exists()).is_false()


@pytest.mark.parametrize(
    "args",
    [("--unknown",), ("--upgrade", "extra")],
    ids=["unknown-flag", "extra-argument"],
)
def test_invalid_compile_arguments_leave_lock_untouched(
    lock_repo: Path,
    args: tuple[str, ...],
) -> None:
    """Reject invalid CLI flags before invoking the resolver."""
    result = _run(root=lock_repo, args=args)
    assert_that(result.returncode).is_equal_to(2)
    assert_that(result.stderr).contains("expected no arguments or --upgrade")
    assert_that((lock_repo / "requirements-semgrep.txt").read_text()).is_equal_to(
        "original\n",
    )
    assert_that((lock_repo / "args").exists()).is_false()


def test_compile_help_documents_upgrade_without_resolving(lock_repo: Path) -> None:
    """Help describes the new mode without changing the lockfile."""
    result = _run(root=lock_repo, args=("--help",))
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("--upgrade", "transitive")
    assert_that((lock_repo / "args").exists()).is_false()


@pytest.mark.parametrize(
    ("dockerfile", "expected"),
    [
        ("ARG UV_VERSION=0.12.19\n", "version=0.12.19\n"),
        ("ARG UV_VERSION=1.2.3\n", "version=1.2.3\n"),
        ("ARG UV_VERSION=latest\n", None),
        ("ARG UV_VERSION=1.2.3\nARG UV_VERSION=4.5.6\n", None),
    ],
    ids=["current-pin", "updated-pin", "missing-exact-pin", "ambiguous-pin"],
)
def test_uv_version_is_derived_from_the_tools_image(
    lock_repo: Path,
    dockerfile: str,
    expected: str | None,
) -> None:
    """Version bumps flow through automatically; absent or ambiguous pins fail closed."""
    (lock_repo / "docker/tools.Dockerfile").write_text(dockerfile)
    result = _run(root=lock_repo, script="read-tools-uv-version.sh")
    if expected is None:
        assert_that(result.returncode).is_equal_to(1)
        assert_that(result.stderr).contains("expected one exact UV_VERSION pin")
        assert_that((lock_repo / "outputs").exists()).is_false()
    else:
        assert_that(result.returncode).is_equal_to(0)
        assert_that((lock_repo / "outputs").read_text()).is_equal_to(expected)


def test_uv_version_help_needs_no_workflow_environment(lock_repo: Path) -> None:
    """Help works without workflow outputs or a tools Dockerfile."""
    (lock_repo / "docker/tools.Dockerfile").unlink()
    result = _run(
        root=lock_repo,
        script="read-tools-uv-version.sh",
        args=("--help",),
        env={"GITHUB_OUTPUT": ""},
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Usage:", "GITHUB_OUTPUT", "UV_VERSION")
    assert_that((lock_repo / "outputs").exists()).is_false()


def _workflow(name: str) -> dict[str, Any]:
    """Load a repository workflow for contract assertions."""
    data: dict[str, Any] = yaml.safe_load(
        (_REPO_ROOT / ".github/workflows" / name).read_text(),
    )
    return data


def test_workflow_restricts_writes_and_uses_the_candidate_signing_contract() -> None:
    """Only canonical Renovate pushes can append a changed lock with the digest app."""
    workflow = _workflow("semgrep-lock-renovate.yml")
    assert_that(workflow["on"]).is_equal_to(
        {
            "push": {
                "branches": ["renovate/semgrep-*", "renovate/lock-file-maintenance"],
            },
        },
    )
    assert_that(workflow["permissions"]).is_empty()
    job = workflow["jobs"]["regenerate"]
    assert_that(job["if"].split()).is_equal_to(
        [
            "github.repository",
            "==",
            "'lgtm-hq/py-lintro'",
            "&&",
            "github.actor",
            "==",
            "'renovate[bot]'",
        ],
    )
    assert_that(job["permissions"]).is_equal_to({"contents": "read"})
    steps = job["steps"]
    candidate = _workflow("docker-tools-candidate.yml")["jobs"]["push-digest"]["steps"]
    actions = {s["uses"].split("@")[0]: s for s in steps if "uses" in s}
    source = {s["uses"].split("@")[0]: s for s in candidate if "uses" in s}
    for action in source:
        assert_that(actions[action]["uses"]).is_equal_to(source[action]["uses"])
    assert_that(steps[0]["uses"]).starts_with("step-security/harden-runner@")
    assert_that(steps[0]["with"]["egress-policy"]).is_equal_to("block")
    checkout = actions["actions/checkout"]
    assert_that(checkout["with"]["persist-credentials"]).is_false()
    assert_that(checkout["with"]["ref"]).is_equal_to("${{ github.sha }}")
    token = actions["actions/create-github-app-token"]
    assert_that(token["with"]).is_equal_to(
        source["actions/create-github-app-token"]["with"],
    )
    commit = actions["lgtm-hq/lgtm-ci/.github/actions/create-signed-commit"]
    for step in (token, commit):
        assert_that(step["if"]).is_equal_to("steps.compile.outputs.changed == 'true'")
    assert_that(commit["with"]["files"].splitlines()).is_equal_to(
        ["requirements-semgrep.txt"],
    )
    for key in ("token", "branch", "mode", "expected-head"):
        assert_that(commit["with"][key]).is_equal_to(
            source["lgtm-hq/lgtm-ci/.github/actions/create-signed-commit"]["with"][key],
        )
    compile_step = next(s for s in steps if s.get("id") == "compile")
    assert_that(compile_step["run"]).is_equal_to(
        "scripts/ci/compile-semgrep-lock.sh "
        "${{ github.ref_name == 'renovate/lock-file-maintenance' && '--upgrade' || '' }}",
    )
    uv = actions["astral-sh/setup-uv"]
    assert_that(uv["with"]["version"]).is_equal_to(
        "${{ steps.uv-version.outputs.version }}",
    )
    assert_that(
        next(s for s in steps if s.get("id") == "uv-version")["run"],
    ).is_equal_to(
        "scripts/ci/read-tools-uv-version.sh",
    )
