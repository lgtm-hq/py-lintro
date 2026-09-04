"""Tests for the report reuse path in dogfood-skip-gate.sh."""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 - fixed script argv in an isolated test
from pathlib import Path

import pytest
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "ci" / "dogfood-skip-gate.sh"

_DOCKER_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >>"${DOCKER_ARGS_LOG}"
if [[ "$*" != *"--entrypoint python3"* ]]; then
\tprevious=
\tfor argument in "$@"; do
\t\tif [[ "$previous" == "--output" ]]; then
\t\t\tprintf '%s\\n' '{"summary":{"total_issues":0},"results":[]}' >"$argument"
\t\tfi
\t\tprevious="$argument"
\tdone
fi
exit 0
"""


def _docker_stub(tmp_path: Path) -> tuple[Path, Path]:
    """Create a Docker stub that records invocations.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        The stub directory and its invocation log path.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_path = tmp_path / "docker-args.log"
    log_path.touch()
    docker = bin_dir / "docker"
    docker.write_text(_DOCKER_STUB)
    docker.chmod(0o755)
    return bin_dir, log_path


def _run_gate(
    *,
    cwd: Path,
    report_json: str | None,
    allowlist: Path,
    bin_dir: Path,
    log_path: Path,
    output_path: Path,
) -> subprocess.CompletedProcess[str]:
    """Run the gate with a pre-existing report and a Docker stub.

    Args:
        cwd: Working directory for the gate.
        report_json: Value for the gate's ``REPORT_JSON`` environment variable,
            or ``None`` to leave it unset.
        allowlist: Allowlist path passed to the gate.
        bin_dir: Directory containing the Docker stub.
        log_path: Docker invocation log path.
        output_path: GitHub output file path.

    Returns:
        The completed gate process.
    """
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "LINTRO_IMAGE": "ghcr.io/example/lintro:ci-test",
        "ALLOWLIST": allowlist.name,
        "DOCKER_ARGS_LOG": str(log_path),
        "GITHUB_OUTPUT": str(output_path),
        "MAP_HOST_USER": "false",
    }
    if report_json is not None:
        env["REPORT_JSON"] = report_json
    run_env = {**os.environ, **env}
    if report_json is None:
        run_env.pop("REPORT_JSON", None)
    return subprocess.run(  # nosec B603 - fixed script argv, shell=False
        [str(_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
        env=run_env,
    )


def _write_report(path: Path) -> None:
    """Write a valid empty lint report to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"summary": {"total_issues": 0}, "results": []}),
    )


@pytest.mark.parametrize(
    "report_json",
    ("results.json", ".lintro/artifacts/json/results.json"),
)
def test_preexisting_report_skips_second_lintro_run_and_maps_report(
    tmp_path: Path,
    report_json: str,
) -> None:
    """Existing reports are reused and mapped below Docker's ``/code`` mount."""
    report = tmp_path / report_json
    _write_report(report)
    allowlist = tmp_path / "allowlist.yaml"
    allowlist.write_text("allowlist: []\n")
    bin_dir, log_path = _docker_stub(tmp_path)
    output_path = tmp_path / "github-output"

    result = _run_gate(
        cwd=tmp_path,
        report_json=report_json,
        allowlist=allowlist,
        bin_dir=bin_dir,
        log_path=log_path,
        output_path=output_path,
    )

    assert_that(result.returncode).is_equal_to(0)
    invocations = log_path.read_text().splitlines()
    assert_that(invocations).contains("pull ghcr.io/example/lintro:ci-test")
    checker_invocation = next(
        invocation for invocation in invocations if "--entrypoint python3" in invocation
    )
    assert_that(checker_invocation).contains(
        f"--report /code/{report_json}",
    )
    assert_that(
        [invocation for invocation in invocations if " chk " in invocation],
    ).is_empty()


def test_absolute_in_workspace_report_maps_to_container_path(tmp_path: Path) -> None:
    """An absolute report inside the mounted workspace is mapped to ``/code``."""
    report = tmp_path / ".lintro" / "artifacts" / "json" / "results.json"
    _write_report(report)
    allowlist = tmp_path / "allowlist.yaml"
    allowlist.write_text("allowlist: []\n")
    bin_dir, log_path = _docker_stub(tmp_path)
    output_path = tmp_path / "github-output"

    result = _run_gate(
        cwd=tmp_path,
        report_json=str(report),
        allowlist=allowlist,
        bin_dir=bin_dir,
        log_path=log_path,
        output_path=output_path,
    )

    assert_that(result.returncode).is_equal_to(0)
    checker_invocation = next(
        invocation
        for invocation in log_path.read_text().splitlines()
        if "--entrypoint python3" in invocation
    )
    assert_that(checker_invocation).contains(
        "--report /code/.lintro/artifacts/json/results.json",
    )


@pytest.mark.parametrize("report_json", (None, ""))
def test_unset_or_empty_report_runs_chk_to_derive_report(
    tmp_path: Path,
    report_json: str | None,
) -> None:
    """An empty report setting keeps the historical in-container ``chk`` run."""
    allowlist = tmp_path / "allowlist.yaml"
    allowlist.write_text("allowlist: []\n")
    bin_dir, log_path = _docker_stub(tmp_path)
    output_path = tmp_path / "github-output"

    result = _run_gate(
        cwd=tmp_path,
        report_json=report_json,
        allowlist=allowlist,
        bin_dir=bin_dir,
        log_path=log_path,
        output_path=output_path,
    )

    assert_that(result.returncode).is_equal_to(0)
    invocations = log_path.read_text().splitlines()
    lint_invocation = next(
        invocation for invocation in invocations if " chk " in invocation
    )
    assert_that(lint_invocation).contains(
        "chk .",
        "--output dogfood-skip-report.json",
    )
    checker_invocation = next(
        invocation for invocation in invocations if "--entrypoint python3" in invocation
    )
    assert_that(checker_invocation).contains(
        "--report /code/dogfood-skip-report.json",
    )


def test_missing_supplied_report_fails_before_docker(tmp_path: Path) -> None:
    """A missing artifact is a configuration error, never a fresh lint run."""
    report = tmp_path / "missing.json"
    allowlist = tmp_path / "allowlist.yaml"
    allowlist.write_text("allowlist: []\n")
    bin_dir, log_path = _docker_stub(tmp_path)
    output_path = tmp_path / "github-output"

    result = _run_gate(
        cwd=tmp_path,
        report_json=report.name,
        allowlist=allowlist,
        bin_dir=bin_dir,
        log_path=log_path,
        output_path=output_path,
    )

    assert_that(result.returncode).is_equal_to(2)
    assert_that(result.stderr).contains("REPORT_JSON")
    assert_that(log_path.read_text()).is_empty()


def test_zero_byte_supplied_report_fails_before_docker_pull(tmp_path: Path) -> None:
    """A zero-byte artifact is rejected before Docker is invoked."""
    report = tmp_path / "results.json"
    report.touch()
    allowlist = tmp_path / "allowlist.yaml"
    allowlist.write_text("allowlist: []\n")
    bin_dir, log_path = _docker_stub(tmp_path)
    output_path = tmp_path / "github-output"

    result = _run_gate(
        cwd=tmp_path,
        report_json=report.name,
        allowlist=allowlist,
        bin_dir=bin_dir,
        log_path=log_path,
        output_path=output_path,
    )

    assert_that(result.returncode).is_equal_to(2)
    assert_that(result.stderr).contains("REPORT_JSON")
    assert_that(log_path.read_text()).is_empty()


def test_absolute_outside_workspace_fails_before_docker(tmp_path: Path) -> None:
    """An absolute report outside the mount is rejected before Docker runs."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    report = tmp_path / "outside.json"
    _write_report(report)
    allowlist = workspace / "allowlist.yaml"
    allowlist.write_text("allowlist: []\n")
    bin_dir, log_path = _docker_stub(tmp_path)
    output_path = tmp_path / "github-output"

    result = _run_gate(
        cwd=workspace,
        report_json=str(report),
        allowlist=allowlist,
        bin_dir=bin_dir,
        log_path=log_path,
        output_path=output_path,
    )

    assert_that(result.returncode).is_equal_to(2)
    assert_that(result.stderr).contains("inside the mounted workspace")
    assert_that(log_path.read_text()).is_empty()


_FAILING_CHECKER_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >>"${DOCKER_ARGS_LOG}"
if [[ "$*" == *"--entrypoint python3"* ]]; then
\texit 1
fi
exit 0
"""


def test_completed_gate_publishes_its_verdict(tmp_path: Path) -> None:
    """A gate that reaches a verdict publishes status/exit-code (#2246).

    The nightly retry and the failure classifier both key off these outputs:
    a published verdict means the gate ran to completion, so the failure (if
    any) is a real non-allowlisted skip rather than a runner kill.
    """
    report = tmp_path / "results.json"
    _write_report(report)
    allowlist = tmp_path / "allowlist.yaml"
    allowlist.write_text("allowlist: []\n")
    bin_dir, log_path = _docker_stub(tmp_path)
    output_path = tmp_path / "github-output"

    result = _run_gate(
        cwd=tmp_path,
        report_json="results.json",
        allowlist=allowlist,
        bin_dir=bin_dir,
        log_path=log_path,
        output_path=output_path,
    )

    assert_that(result.returncode).is_equal_to(0)
    written = output_path.read_text()
    assert_that(written).contains("status=passed")
    assert_that(written).contains("exit-code=0")


def test_failing_skip_check_publishes_a_failed_verdict(tmp_path: Path) -> None:
    """A non-allowlisted skip is reported as a verdict, not as a missing one."""
    report = tmp_path / "results.json"
    _write_report(report)
    allowlist = tmp_path / "allowlist.yaml"
    allowlist.write_text("allowlist: []\n")
    bin_dir, log_path = _docker_stub(tmp_path)
    (bin_dir / "docker").write_text(_FAILING_CHECKER_STUB)
    (bin_dir / "docker").chmod(0o755)
    output_path = tmp_path / "github-output"

    result = _run_gate(
        cwd=tmp_path,
        report_json="results.json",
        allowlist=allowlist,
        bin_dir=bin_dir,
        log_path=log_path,
        output_path=output_path,
    )

    assert_that(result.returncode).is_equal_to(1)
    written = output_path.read_text()
    assert_that(written).contains("status=failed")
    assert_that(written).contains("exit-code=1")


_CHECKER_EXIT_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >>"${DOCKER_ARGS_LOG}"
if [[ "$*" == *"--entrypoint python3"* ]]; then
\texit "${CHECKER_EXIT_CODE}"
fi
exit 0
"""


@pytest.mark.parametrize("checker_exit_code", ["2", "143"])
def test_non_verdict_checker_exit_publishes_no_verdict(
    tmp_path: Path,
    checker_exit_code: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the checker's 0/1 verdict codes publish status and exit-code.

    A report/allowlist error (2) or a docker-side kill (143) reaching the
    publish step must leave both outputs absent, so the nightly retry (which
    requires an empty status) and the classifier see a missing verdict rather
    than a fabricated failed one (#2246).
    """
    report = tmp_path / "results.json"
    _write_report(report)
    allowlist = tmp_path / "allowlist.yaml"
    allowlist.write_text("allowlist: []\n")
    bin_dir, log_path = _docker_stub(tmp_path)
    (bin_dir / "docker").write_text(_CHECKER_EXIT_STUB)
    (bin_dir / "docker").chmod(0o755)
    monkeypatch.setenv("CHECKER_EXIT_CODE", checker_exit_code)
    output_path = tmp_path / "github-output"
    output_path.touch()

    result = _run_gate(
        cwd=tmp_path,
        report_json="results.json",
        allowlist=allowlist,
        bin_dir=bin_dir,
        log_path=log_path,
        output_path=output_path,
    )

    assert_that(result.returncode).is_equal_to(int(checker_exit_code))
    written = output_path.read_text()
    assert_that(written).does_not_contain("status=")
    assert_that(written).does_not_contain("exit-code=")


def test_configuration_error_publishes_no_verdict(tmp_path: Path) -> None:
    """A gate that never reaches the skip check leaves the verdict empty.

    That absence is what a killed runner looks like to the nightly workflow,
    so it must never be filled in by a path that did not check anything.
    """
    allowlist = tmp_path / "allowlist.yaml"
    allowlist.write_text("allowlist: []\n")
    bin_dir, log_path = _docker_stub(tmp_path)
    output_path = tmp_path / "github-output"
    output_path.touch()

    result = _run_gate(
        cwd=tmp_path,
        report_json="missing-report.json",
        allowlist=allowlist,
        bin_dir=bin_dir,
        log_path=log_path,
        output_path=output_path,
    )

    assert_that(result.returncode).is_equal_to(2)
    assert_that(output_path.read_text()).does_not_contain("status=")
