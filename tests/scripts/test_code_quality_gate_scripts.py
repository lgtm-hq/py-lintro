"""Tests for code-quality gate and assert-required-check shell scripts."""

from __future__ import annotations

import os
import subprocess  # nosec B404 - subprocess drives shell scripts under test; shell=False
import tempfile
from pathlib import Path

import pytest
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_script(
    script: str,
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    script_path = (_REPO_ROOT / script).resolve()
    return subprocess.run(  # nosec B603 - fixed argv against repo scripts; shell=False
        [str(script_path)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ.copy(), **(env or {})},
    )


@pytest.mark.parametrize(
    "script",
    [
        "scripts/ci/is-infra-flake-failure.sh",
        "scripts/ci/assert-required-check.sh",
        "scripts/ci/evaluate-code-quality-gate.sh",
        "scripts/ci/run-code-quality-gate.sh",
    ],
)
def test_code_quality_gate_scripts_expose_help(script: str) -> None:
    """Each gate helper script should support --help."""
    script_path = (_REPO_ROOT / script).resolve()
    result = (
        subprocess.run(  # nosec B603 - fixed argv against repo scripts; shell=False
            [str(script_path), "--help"],
            capture_output=True,
            text=True,
            check=False,
            env=os.environ.copy(),
        )
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Usage:")


@pytest.mark.parametrize(
    ("result", "status", "exit_code", "conclusion", "reason", "expected_infra"),
    [
        ("cancelled", "", "", "", "", True),
        ("failure", "", "", "cancelled", "", True),
        ("failure", "", "", "timed_out", "", True),
        ("failure", "", "143", "", "", True),
        ("failure", "", "", "", "runner shutdown signal", True),
        ("failure", "", "", "", "Failed to CreateArtifact: ETIMEDOUT", True),
        ("failure", "", "", "", "", True),
        ("failure", "failed", "1", "", "", False),
        ("success", "passed", "0", "", "", False),
    ],
)
def test_is_infra_flake_failure_classification(
    *,
    result: str,
    status: str,
    exit_code: str,
    conclusion: str,
    reason: str,
    expected_infra: bool,
) -> None:
    """Infra flake classifier should match shutdown and lint-failure cases."""
    proc = _run_script(
        "scripts/ci/is-infra-flake-failure.sh",
        env={
            "UPSTREAM_RESULT": result,
            "STATUS_OUTPUT": status,
            "EXIT_CODE_OUTPUT": exit_code,
            "UPSTREAM_CONCLUSION": conclusion,
            "FAILURE_REASON": reason,
        },
    )
    if expected_infra:
        assert_that(proc.returncode).is_equal_to(0)
    else:
        assert_that(proc.returncode).is_equal_to(1)


def test_assert_required_check_passes_on_success() -> None:
    """assert-required-check should pass when upstream succeeded."""
    result = _run_script(
        "scripts/ci/assert-required-check.sh",
        env={
            "UPSTREAM_RESULT": "success",
            "STATUS_OUTPUT": "passed",
            "STATUS_EXPECTED": "passed",
        },
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Required check satisfied")


def test_assert_required_check_treats_cancelled_as_infra_flake() -> None:
    """assert-required-check should not fail on infra-cancelled upstream jobs."""
    result = _run_script(
        "scripts/ci/assert-required-check.sh",
        env={
            "UPSTREAM_RESULT": "cancelled",
        },
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("infra flake")


def test_assert_required_check_fails_on_genuine_lint_failure() -> None:
    """assert-required-check should fail on real lint failures."""
    result = _run_script(
        "scripts/ci/assert-required-check.sh",
        env={
            "UPSTREAM_RESULT": "failure",
            "STATUS_OUTPUT": "failed",
            "EXIT_CODE_OUTPUT": "1",
        },
    )
    assert_that(result.returncode).is_equal_to(1)
    assert_that(result.stderr + result.stdout).contains("Upstream job failed")


def test_evaluate_code_quality_gate_prefers_retry_success() -> None:
    """Gate evaluation should use retry outputs when the retry job ran."""
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as output_file:
        output_path = output_file.name

    try:
        result = _run_script(
            "scripts/ci/evaluate-code-quality-gate.sh",
            env={
                "GITHUB_OUTPUT": output_path,
                "DOCKER_BUILD_RESULT": "success",
                "MANIFEST_SYNC_RESULT": "success",
                "PRIMARY_LINT_RESULT": "failure",
                "RETRY_LINT_RESULT": "success",
                "PRIMARY_LINT_STATUS": "",
                "PRIMARY_LINT_EXIT_CODE": "",
                "RETRY_LINT_STATUS": "passed",
                "RETRY_LINT_EXIT_CODE": "0",
            },
        )
        assert_that(result.returncode).is_equal_to(0)
        output = Path(output_path).read_text()
        assert_that(output).contains("upstream-result=success")
        assert_that(output).contains("status-output=passed")
        assert_that(output).contains("exit-code-output=0")
    finally:
        Path(output_path).unlink(missing_ok=True)


def test_evaluate_code_quality_gate_propagates_docker_build_failure() -> None:
    """Gate evaluation should short-circuit on docker-build failure."""
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as output_file:
        output_path = output_file.name

    try:
        result = _run_script(
            "scripts/ci/evaluate-code-quality-gate.sh",
            env={
                "GITHUB_OUTPUT": output_path,
                "DOCKER_BUILD_RESULT": "failure",
                "MANIFEST_SYNC_RESULT": "success",
                "PRIMARY_LINT_RESULT": "skipped",
            },
        )
        assert_that(result.returncode).is_equal_to(0)
        output = Path(output_path).read_text()
        assert_that(output).contains("upstream-result=failure")
        assert_that(output).contains("status-output=failed")
        assert_that(output).contains("exit-code-output=1")
    finally:
        Path(output_path).unlink(missing_ok=True)


def test_run_code_quality_gate_fails_on_docker_build_failure() -> None:
    """End-to-end gate should fail when docker-build did not succeed."""
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as output_file:
        output_path = output_file.name

    try:
        result = _run_script(
            "scripts/ci/run-code-quality-gate.sh",
            env={
                "GITHUB_OUTPUT": output_path,
                "DOCKER_BUILD_RESULT": "failure",
                "MANIFEST_SYNC_RESULT": "success",
                "PRIMARY_LINT_RESULT": "skipped",
            },
        )
        assert_that(result.returncode).is_equal_to(1)
        output = Path(output_path).read_text()
        assert_that(output).contains("result=failure")
        assert_that(output).contains("passed=false")
    finally:
        Path(output_path).unlink(missing_ok=True)


def test_run_code_quality_gate_passes_after_infra_flake() -> None:
    """End-to-end gate should pass when lint failed without outputs (shutdown)."""
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as output_file:
        output_path = output_file.name

    try:
        result = _run_script(
            "scripts/ci/run-code-quality-gate.sh",
            env={
                "GITHUB_OUTPUT": output_path,
                "DOCKER_BUILD_RESULT": "success",
                "MANIFEST_SYNC_RESULT": "success",
                "PRIMARY_LINT_RESULT": "failure",
                "RETRY_LINT_RESULT": "skipped",
                "PRIMARY_LINT_STATUS": "",
                "PRIMARY_LINT_EXIT_CODE": "",
            },
        )
        assert_that(result.returncode).is_equal_to(0)
        output = Path(output_path).read_text()
        assert_that(output).contains("result=success")
        assert_that(output).contains("passed=true")
    finally:
        Path(output_path).unlink(missing_ok=True)


def test_run_code_quality_gate_fails_when_retry_reports_real_lint_failure() -> None:
    """A failed retry with real outputs must not be treated as an infra flake."""
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as output_file:
        output_path = output_file.name

    try:
        result = _run_script(
            "scripts/ci/run-code-quality-gate.sh",
            env={
                "GITHUB_OUTPUT": output_path,
                "DOCKER_BUILD_RESULT": "success",
                "MANIFEST_SYNC_RESULT": "success",
                "PRIMARY_LINT_RESULT": "failure",
                "RETRY_LINT_RESULT": "failure",
                "PRIMARY_LINT_STATUS": "",
                "PRIMARY_LINT_EXIT_CODE": "",
                "RETRY_LINT_STATUS": "failed",
                "RETRY_LINT_EXIT_CODE": "1",
            },
        )
        assert_that(result.returncode).is_equal_to(1)
        output = Path(output_path).read_text()
        assert_that(output).contains("result=failure")
        assert_that(output).contains("passed=false")
    finally:
        Path(output_path).unlink(missing_ok=True)
