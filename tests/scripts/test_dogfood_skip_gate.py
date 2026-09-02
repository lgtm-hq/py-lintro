"""Tests for the report reuse path in dogfood-skip-gate.sh."""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 - fixed script argv in an isolated test
from pathlib import Path

from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "ci" / "dogfood-skip-gate.sh"

_DOCKER_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >>"${DOCKER_ARGS_LOG}"
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
    report: Path,
    allowlist: Path,
    bin_dir: Path,
    log_path: Path,
    output_path: Path,
) -> subprocess.CompletedProcess[str]:
    """Run the gate with a pre-existing report and a Docker stub.

    Args:
        report: Existing lint JSON report path.
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
        "REPORT_JSON": report.name,
        "ALLOWLIST": allowlist.name,
        "DOCKER_ARGS_LOG": str(log_path),
        "GITHUB_OUTPUT": str(output_path),
        "MAP_HOST_USER": "false",
    }
    return subprocess.run(  # nosec B603 - fixed script argv, shell=False
        [str(_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        cwd=report.parent,
        env={**os.environ, **env},
    )


def test_preexisting_report_skips_second_lintro_run(tmp_path: Path) -> None:
    """A supplied report is classified without invoking lintro in Docker."""
    report = tmp_path / "results.json"
    report.write_text(
        json.dumps({"summary": {"total_issues": 0}, "results": []}),
    )
    allowlist = tmp_path / "allowlist.yaml"
    allowlist.write_text("allowlist: []\n")
    bin_dir, log_path = _docker_stub(tmp_path)
    output_path = tmp_path / "github-output"

    result = _run_gate(
        report=report,
        allowlist=allowlist,
        bin_dir=bin_dir,
        log_path=log_path,
        output_path=output_path,
    )

    assert_that(result.returncode).is_equal_to(0)
    invocations = log_path.read_text().splitlines()
    assert_that(invocations).contains("pull ghcr.io/example/lintro:ci-test")
    assert_that(" ".join(invocations)).contains("--entrypoint python3")
    assert_that(" ".join(invocations)).does_not_contain(" chk ")


def test_missing_supplied_report_fails_before_docker(tmp_path: Path) -> None:
    """A missing artifact is a configuration error, never a fresh lint run."""
    report = tmp_path / "missing.json"
    allowlist = tmp_path / "allowlist.yaml"
    allowlist.write_text("allowlist: []\n")
    bin_dir, log_path = _docker_stub(tmp_path)
    output_path = tmp_path / "github-output"

    result = _run_gate(
        report=report,
        allowlist=allowlist,
        bin_dir=bin_dir,
        log_path=log_path,
        output_path=output_path,
    )

    assert_that(result.returncode).is_equal_to(2)
    assert_that(result.stderr).contains("REPORT_JSON")
    assert_that(log_path.read_text()).is_empty()
