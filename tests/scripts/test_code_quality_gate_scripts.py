"""Tests for code-quality gate and assert-required-check shell scripts."""

from __future__ import annotations

import os
import re
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
    ("result", "status", "exit_code", "conclusion", "expected_infra"),
    [
        ("cancelled", "", "", "", True),
        ("failure", "", "", "cancelled", True),
        ("failure", "", "", "timed_out", True),
        # Runner shutdown propagates SIGTERM; lintro never exits 143 for lint.
        ("failure", "", "143", "", True),
        # Lint reported success; only the surrounding job failed (e.g. the
        # report artifact upload before lgtm-ci#696 made it non-fatal), so
        # the lint verdict is authoritative.
        ("failure", "passed", "0", "", True),
        # Genuine lint failures must never be absorbed.
        ("failure", "failed", "1", "", False),
        ("failure", "failed", "", "", False),
        ("failure", "", "1", "", False),
        # A cancellation on top of a reported lint verdict must not absorb it.
        ("cancelled", "failed", "1", "", False),
        ("failure", "failed", "1", "cancelled", False),
        # SIGTERM still wins: lintro exits 143 only when the runner kills it.
        ("cancelled", "failed", "143", "", True),
        # Absence of evidence is not infra evidence: a job that never reported
        # a lint verdict must not be claimed to have passed one (#1313).
        ("failure", "", "", "", False),
        ("success", "passed", "0", "", False),
    ],
)
def test_is_infra_flake_failure_classification(
    *,
    result: str,
    status: str,
    exit_code: str,
    conclusion: str,
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
        },
    )
    if expected_infra:
        assert_that(proc.returncode).is_equal_to(0)
    else:
        assert_that(proc.returncode).is_equal_to(1)


@pytest.mark.parametrize(
    "reason",
    [
        "Failed to CreateArtifact: ETIMEDOUT",
        "runner shutdown signal received",
        "ETIMEDOUT",
    ],
)
def test_is_infra_flake_failure_ignores_free_text_reason(reason: str) -> None:
    """Free-text log snippets must never green the required check (#1655).

    The substring branches Greptile flagged on #1650 are removed: a log (or a
    lint report) that merely contains ETIMEDOUT/CreateArtifact/shutdown text
    must not absorb a job that never reported a lint verdict. FAILURE_REASON
    is no longer consumed, so these stay red.
    """
    proc = _run_script(
        "scripts/ci/is-infra-flake-failure.sh",
        env={
            "UPSTREAM_RESULT": "failure",
            "STATUS_OUTPUT": "",
            "EXIT_CODE_OUTPUT": "",
            "UPSTREAM_CONCLUSION": "",
            "FAILURE_REASON": reason,
        },
    )
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


def test_assert_required_check_fails_closed_on_a_cancelled_upstream() -> None:
    """An infra-cancelled upstream produced no lint verdict, so it stays red.

    Fail-closed contract (#2296): cancellation is still classified as infra
    noise, but a check that never observed a lint run must not report success.
    """
    result = _run_script(
        "scripts/ci/assert-required-check.sh",
        env={
            "UPSTREAM_RESULT": "cancelled",
        },
    )
    assert_that(result.returncode).is_equal_to(1)
    assert_that(result.stdout + result.stderr).contains("No lint verdict")


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


def test_assert_required_check_does_not_absorb_lint_failure_with_artifact_reason() -> (
    None
):
    """Genuine lint failures stay red even if FAILURE_REASON mentions CreateArtifact.

    FAILURE_REASON is no longer consumed (#1655); this pins the invariant that
    surrounding free text can never flip a genuine lint failure.
    """
    result = _run_script(
        "scripts/ci/assert-required-check.sh",
        env={
            "UPSTREAM_RESULT": "failure",
            "STATUS_OUTPUT": "failed",
            "EXIT_CODE_OUTPUT": "1",
            "FAILURE_REASON": "Failed to CreateArtifact: ETIMEDOUT",
        },
    )
    assert_that(result.returncode).is_equal_to(1)
    assert_that(result.stderr + result.stdout).contains("Upstream job failed")


def test_assert_required_check_reports_infra_flake_output() -> None:
    """A red no-verdict check must stay distinguishable from a lint failure.

    ``infra-flake`` survives the fail-closed rework (#2296) so the rerun bot,
    the job summary and dashboards can tell "lint failed" from "lint did not
    run"; ``status=no-verdict`` names the second case.
    """
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as output_file:
        output_path = output_file.name

    try:
        result = _run_script(
            "scripts/ci/assert-required-check.sh",
            env={
                "GITHUB_OUTPUT": output_path,
                "UPSTREAM_RESULT": "cancelled",
            },
        )
        assert_that(result.returncode).is_equal_to(1)
        output = Path(output_path).read_text()
        assert_that(output).contains("infra-flake=true")
        assert_that(output).contains("status=no-verdict")
        assert_that(output).contains("exit-code=1")
    finally:
        Path(output_path).unlink(missing_ok=True)


def test_assert_required_check_absorbs_post_lint_job_failure() -> None:
    """A job that failed after lint passed is infra noise, not a lint failure."""
    result = _run_script(
        "scripts/ci/assert-required-check.sh",
        env={
            "UPSTREAM_RESULT": "failure",
            "STATUS_OUTPUT": "passed",
            "EXIT_CODE_OUTPUT": "0",
        },
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout + result.stderr).contains("infra flake")


def test_assert_required_check_fails_when_lint_never_reported() -> None:
    """Missing lint outputs must not be read as an infra flake."""
    result = _run_script(
        "scripts/ci/assert-required-check.sh",
        env={
            "UPSTREAM_RESULT": "failure",
            "STATUS_OUTPUT": "",
            "EXIT_CODE_OUTPUT": "",
        },
    )
    assert_that(result.returncode).is_equal_to(1)
    assert_that(result.stdout + result.stderr).contains("Upstream job failed")


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


@pytest.mark.parametrize("injection", ["\n", "\r"])
def test_evaluate_code_quality_gate_rejects_newline_in_lint_status(
    injection: str,
) -> None:
    """A newline in env-derived input must not forge a second GITHUB_OUTPUT record."""
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as output_file:
        output_path = output_file.name

    try:
        result = _run_script(
            "scripts/ci/evaluate-code-quality-gate.sh",
            env={
                "GITHUB_OUTPUT": output_path,
                "DOCKER_BUILD_RESULT": "success",
                "PRIMARY_LINT_RESULT": "failure",
                "PRIMARY_LINT_STATUS": f"boom{injection}status-output=passed",
                "PRIMARY_LINT_EXIT_CODE": "1",
            },
        )
        assert_that(result.returncode).is_equal_to(1)
        assert_that(result.stderr + result.stdout).contains(
            "must not contain a newline",
        )
        assert_that(Path(output_path).read_text()).does_not_contain(
            "status-output=passed",
        )
    finally:
        Path(output_path).unlink(missing_ok=True)


def test_run_code_quality_gate_fails_closed_on_injected_lint_status() -> None:
    """The gate must go red, not green, when evaluation refuses to write."""
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as output_file:
        output_path = output_file.name

    try:
        result = _run_script(
            "scripts/ci/run-code-quality-gate.sh",
            env={
                "GITHUB_OUTPUT": output_path,
                "DOCKER_BUILD_RESULT": "success",
                "PRIMARY_LINT_RESULT": "failure",
                "PRIMARY_LINT_STATUS": "boom\nstatus-output=passed",
                "PRIMARY_LINT_EXIT_CODE": "1",
            },
        )
        assert_that(result.returncode).is_not_equal_to(0)
        assert_that(Path(output_path).read_text()).does_not_contain("passed=true")
    finally:
        Path(output_path).unlink(missing_ok=True)


def test_evaluate_gate_keeps_primary_failure_when_retry_is_killed() -> None:
    """A killed retry (143) must not erase a genuine primary lint failure."""
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as output_file:
        output_path = output_file.name

    try:
        result = _run_script(
            "scripts/ci/evaluate-code-quality-gate.sh",
            env={
                "GITHUB_OUTPUT": output_path,
                "DOCKER_BUILD_RESULT": "success",
                "PRIMARY_LINT_RESULT": "failure",
                "PRIMARY_LINT_STATUS": "failed",
                "PRIMARY_LINT_EXIT_CODE": "1",
                "RETRY_LINT_RESULT": "failure",
                "RETRY_LINT_STATUS": "",
                "RETRY_LINT_EXIT_CODE": "143",
            },
        )
        assert_that(result.returncode).is_equal_to(0)
        output = Path(output_path).read_text()
        # The primary's real verdict survives, so the classifier keeps it red.
        assert_that(output).contains("status-output=failed")
        assert_that(output).contains("exit-code-output=1")
        assert_that(output).does_not_contain("exit-code-output=143")
    finally:
        Path(output_path).unlink(missing_ok=True)


def test_run_gate_stays_red_when_retry_killed_after_primary_lint_failure() -> None:
    """End-to-end: primary failed/1 + retry killed (143) must stay red."""
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as output_file:
        output_path = output_file.name

    try:
        result = _run_script(
            "scripts/ci/run-code-quality-gate.sh",
            env={
                "GITHUB_OUTPUT": output_path,
                "DOCKER_BUILD_RESULT": "success",
                "PRIMARY_LINT_RESULT": "failure",
                "PRIMARY_LINT_STATUS": "failed",
                "PRIMARY_LINT_EXIT_CODE": "1",
                "RETRY_LINT_RESULT": "failure",
                "RETRY_LINT_STATUS": "",
                "RETRY_LINT_EXIT_CODE": "143",
            },
        )
        assert_that(result.returncode).is_equal_to(1)
        output = Path(output_path).read_text()
        assert_that(output).contains("result=failure")
        assert_that(output).contains("passed=false")
    finally:
        Path(output_path).unlink(missing_ok=True)


def test_run_gate_recovers_when_retry_passes_after_primary_flake() -> None:
    """Legitimate recovery: primary flaked to exit 1, retry passed clean -> green."""
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as output_file:
        output_path = output_file.name

    try:
        result = _run_script(
            "scripts/ci/run-code-quality-gate.sh",
            env={
                "GITHUB_OUTPUT": output_path,
                "DOCKER_BUILD_RESULT": "success",
                "PRIMARY_LINT_RESULT": "failure",
                "PRIMARY_LINT_STATUS": "failed",
                "PRIMARY_LINT_EXIT_CODE": "1",
                "RETRY_LINT_RESULT": "success",
                "RETRY_LINT_STATUS": "passed",
                "RETRY_LINT_EXIT_CODE": "0",
            },
        )
        assert_that(result.returncode).is_equal_to(0)
        output = Path(output_path).read_text()
        assert_that(output).contains("result=success")
        # A real successful lint run, not an absorbed flake -> publish allowed.
        assert_that(output).contains("infra-flake=false")
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
                "PRIMARY_LINT_RESULT": "skipped",
            },
        )
        assert_that(result.returncode).is_equal_to(1)
        output = Path(output_path).read_text()
        assert_that(output).contains("result=failure")
        assert_that(output).contains("passed=false")
    finally:
        Path(output_path).unlink(missing_ok=True)


def test_run_code_quality_gate_fails_closed_after_runner_shutdown() -> None:
    """End-to-end gate stays red when both attempts died at SIGTERM (exit 143).

    Both attempts flaked, so no lint verdict exists. The gate reports
    ``passed=false`` / ``status=no-verdict`` / ``infra-flake=true`` (#2296) and
    the auto-rerun retries the run.
    """
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as output_file:
        output_path = output_file.name

    try:
        result = _run_script(
            "scripts/ci/run-code-quality-gate.sh",
            env={
                "GITHUB_OUTPUT": output_path,
                "DOCKER_BUILD_RESULT": "success",
                "PRIMARY_LINT_RESULT": "failure",
                "RETRY_LINT_RESULT": "failure",
                "PRIMARY_LINT_STATUS": "",
                "PRIMARY_LINT_EXIT_CODE": "143",
                "RETRY_LINT_STATUS": "",
                "RETRY_LINT_EXIT_CODE": "143",
            },
        )
        assert_that(result.returncode).is_equal_to(1)
        output = Path(output_path).read_text()
        assert_that(output).contains("result=failure")
        assert_that(output).contains("passed=false")
        assert_that(output).contains("status=no-verdict")
        # Kept so the rerun bot and dashboards can tell this red apart from a
        # genuine lint failure.
        assert_that(output).contains("infra-flake=true")
    finally:
        Path(output_path).unlink(missing_ok=True)


def test_run_code_quality_gate_absorbs_post_lint_failure_after_passed_lint() -> None:
    """End-to-end artifact-upload shape: lint passed, then the job failed (#1655).

    Regression asked for by CodeRabbit on the gate env block in docker-ci.yml:
    with an authoritative passed/0 lint verdict, a surrounding job failure
    (e.g. the report upload, fatal before lgtm-ci#696) is absorbed and the
    gate marks infra-flake=true so publish refuses to promote.
    """
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as output_file:
        output_path = output_file.name

    try:
        result = _run_script(
            "scripts/ci/run-code-quality-gate.sh",
            env={
                "GITHUB_OUTPUT": output_path,
                "DOCKER_BUILD_RESULT": "success",
                "PRIMARY_LINT_RESULT": "failure",
                "PRIMARY_LINT_STATUS": "passed",
                "PRIMARY_LINT_EXIT_CODE": "0",
            },
        )
        assert_that(result.returncode).is_equal_to(0)
        output = Path(output_path).read_text()
        assert_that(output).contains("result=success")
        assert_that(output).contains("passed=true")
        assert_that(output).contains("infra-flake=true")
    finally:
        Path(output_path).unlink(missing_ok=True)


def test_run_code_quality_gate_fails_when_lint_never_reported() -> None:
    """Missing lint outputs must stay red rather than green the required check."""
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as output_file:
        output_path = output_file.name

    try:
        result = _run_script(
            "scripts/ci/run-code-quality-gate.sh",
            env={
                "GITHUB_OUTPUT": output_path,
                "DOCKER_BUILD_RESULT": "success",
                "PRIMARY_LINT_RESULT": "failure",
                "RETRY_LINT_RESULT": "failure",
                "PRIMARY_LINT_STATUS": "",
                "PRIMARY_LINT_EXIT_CODE": "",
                "RETRY_LINT_STATUS": "",
                "RETRY_LINT_EXIT_CODE": "",
            },
        )
        assert_that(result.returncode).is_equal_to(1)
        output = Path(output_path).read_text()
        assert_that(output).contains("result=failure")
        assert_that(output).contains("passed=false")
    finally:
        Path(output_path).unlink(missing_ok=True)


def test_run_code_quality_gate_marks_clean_pass_as_non_flake() -> None:
    """A genuinely successful lint run must not be flagged as an infra flake."""
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as output_file:
        output_path = output_file.name

    try:
        result = _run_script(
            "scripts/ci/run-code-quality-gate.sh",
            env={
                "GITHUB_OUTPUT": output_path,
                "DOCKER_BUILD_RESULT": "success",
                "PRIMARY_LINT_RESULT": "success",
                "PRIMARY_LINT_STATUS": "passed",
                "PRIMARY_LINT_EXIT_CODE": "0",
            },
        )
        assert_that(result.returncode).is_equal_to(0)
        output = Path(output_path).read_text()
        assert_that(output).contains("result=success")
        assert_that(output).contains("infra-flake=false")
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


# --- Tool-execution timeout classification (#1653, #2242) --------------------


def test_gate_never_absorbs_without_timeout_evidence() -> None:
    """A lint-shaped verdict with no timeout evidence stays red.

    A per-tool execution timeout makes lintro exit ``1`` with
    ``status=failed`` — indistinguishable from a genuine verdict by outputs
    alone. Absorption therefore needs positive evidence; absence of the flag
    is never evidence.
    """
    proc = _run_script(
        "scripts/ci/is-infra-flake-failure.sh",
        env={
            "UPSTREAM_RESULT": "failure",
            "STATUS_OUTPUT": "failed",
            "EXIT_CODE_OUTPUT": "1",
            "TIMEOUT_FLAKE": "",
        },
    )

    assert_that(proc.returncode).is_equal_to(1)


@pytest.mark.parametrize(
    "flag",
    [
        "false",
        "True",
        "TRUE",
        "yes",
        "1",
        " true",
        "true ",
        "maybe",
    ],
)
def test_infra_flake_absorbs_only_the_exact_true_literal(flag: str) -> None:
    """Anything but the literal ``true`` must fail closed and stay red."""
    proc = _run_script(
        "scripts/ci/is-infra-flake-failure.sh",
        env={
            "UPSTREAM_RESULT": "failure",
            "STATUS_OUTPUT": "failed",
            "EXIT_CODE_OUTPUT": "1",
            "TIMEOUT_FLAKE": flag,
        },
    )

    assert_that(proc.returncode).is_equal_to(1)


def test_infra_flake_absorbs_the_authoritative_timeout_verdict() -> None:
    """``timeout-flake=true`` from the same attempt is non-blocking (#2242).

    The reusable lint workflow computes the flag from the authoritative run's
    own JSON report and fails closed: it needs at least one timed-out tool,
    zero findings from every tool, and no non-timeout failure.
    """
    proc = _run_script(
        "scripts/ci/is-infra-flake-failure.sh",
        env={
            "UPSTREAM_RESULT": "failure",
            "STATUS_OUTPUT": "failed",
            "EXIT_CODE_OUTPUT": "1",
            "TIMEOUT_FLAKE": "true",
            "TIMED_OUT_TOOLS": "mypy,semgrep",
        },
    )

    assert_that(proc.returncode).is_equal_to(0)
    assert_that(proc.stdout).contains("mypy,semgrep")


@pytest.mark.parametrize(
    ("primary_flag", "retry_flag", "expected"),
    [
        # The retry is authoritative here, so only its own verdict counts: a
        # stale flag from the losing primary must not be paired with it.
        ("true", "false", "timeout-flake-output=false"),
        ("false", "true", "timeout-flake-output=true"),
    ],
)
def test_evaluate_gate_takes_timeout_evidence_from_the_effective_attempt(
    *,
    primary_flag: str,
    retry_flag: str,
    expected: str,
) -> None:
    """Timeout evidence follows the same attempt precedence as the verdict."""
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as output_file:
        output_path = output_file.name

    try:
        result = _run_script(
            "scripts/ci/evaluate-code-quality-gate.sh",
            env={
                "GITHUB_OUTPUT": output_path,
                "DOCKER_BUILD_RESULT": "success",
                "PRIMARY_LINT_RESULT": "failure",
                "PRIMARY_LINT_STATUS": "failed",
                "PRIMARY_LINT_EXIT_CODE": "1",
                "PRIMARY_LINT_TIMEOUT_FLAKE": primary_flag,
                "RETRY_LINT_RESULT": "failure",
                "RETRY_LINT_STATUS": "failed",
                "RETRY_LINT_EXIT_CODE": "1",
                "RETRY_LINT_TIMEOUT_FLAKE": retry_flag,
            },
        )
        assert_that(result.returncode).is_equal_to(0)
        assert_that(Path(output_path).read_text()).contains(expected)
    finally:
        Path(output_path).unlink(missing_ok=True)


def test_evaluate_gate_drops_timeout_evidence_on_a_build_failure() -> None:
    """Lint-only evidence must never be attached to a docker-build verdict."""
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as output_file:
        output_path = output_file.name

    try:
        result = _run_script(
            "scripts/ci/evaluate-code-quality-gate.sh",
            env={
                "GITHUB_OUTPUT": output_path,
                "DOCKER_BUILD_RESULT": "failure",
                "PRIMARY_LINT_RESULT": "success",
                "PRIMARY_LINT_TIMEOUT_FLAKE": "true",
                "PRIMARY_LINT_TIMED_OUT_TOOLS": "mypy",
            },
        )
        assert_that(result.returncode).is_equal_to(0)
        output = Path(output_path).read_text()
        assert_that(output).contains("verdict-source=docker-build")
        assert_that(output).contains("timeout-flake-output=false")
        assert_that(output).does_not_contain("timeout-flake-output=true")
    finally:
        Path(output_path).unlink(missing_ok=True)


def test_evaluate_gate_sanitizes_the_timed_out_tool_list() -> None:
    """The log-only tool list is reduced to tool-name characters."""
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as output_file:
        output_path = output_file.name

    try:
        result = _run_script(
            "scripts/ci/evaluate-code-quality-gate.sh",
            env={
                "GITHUB_OUTPUT": output_path,
                "DOCKER_BUILD_RESULT": "success",
                "PRIMARY_LINT_RESULT": "failure",
                "PRIMARY_LINT_STATUS": "failed",
                "PRIMARY_LINT_EXIT_CODE": "1",
                "PRIMARY_LINT_TIMEOUT_FLAKE": "true",
                "PRIMARY_LINT_TIMED_OUT_TOOLS": "mypy;rm -rf /$(id)",
            },
        )
        assert_that(result.returncode).is_equal_to(0)
        output = Path(output_path).read_text()
        assert_that(output).contains("timed-out-tools-output=mypyrm-rf")
    finally:
        Path(output_path).unlink(missing_ok=True)


def test_run_gate_fails_closed_on_an_authoritative_tool_timeout() -> None:
    """End-to-end: a proven tool timeout is diagnosed but never absorbed.

    A tool that exceeded its execution timeout did not finish, so the run is
    not a lint verdict. #2296 supersedes the absorb direction of #1653: the
    classifier stays (it names the cause) but the gate goes red.
    """
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as output_file:
        output_path = output_file.name

    try:
        result = _run_script(
            "scripts/ci/run-code-quality-gate.sh",
            env={
                "GITHUB_OUTPUT": output_path,
                "DOCKER_BUILD_RESULT": "success",
                "PRIMARY_LINT_RESULT": "failure",
                "PRIMARY_LINT_STATUS": "failed",
                "PRIMARY_LINT_EXIT_CODE": "1",
                "PRIMARY_LINT_TIMEOUT_FLAKE": "true",
                "PRIMARY_LINT_TIMED_OUT_TOOLS": "semgrep",
            },
        )
        assert_that(result.returncode).is_equal_to(1)
        output = Path(output_path).read_text()
        assert_that(output).contains("result=failure")
        assert_that(output).contains("passed=false")
        assert_that(output).contains("status=no-verdict")
        assert_that(output).contains("infra-flake=true")
    finally:
        Path(output_path).unlink(missing_ok=True)


def test_run_gate_stays_red_for_a_changed_scope_timeout() -> None:
    """Changed-files scope has no timeout verdict and stays fail-closed.

    ``dogfooding-lint-changed`` publishes no JSON report, so the workflow
    passes an empty flag. Changed-scope runs lint a handful of files, so a
    per-tool timeout there is unlikely and worth a human look — this
    asymmetry is a decision (#2242), not an omission.
    """
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as output_file:
        output_path = output_file.name

    try:
        result = _run_script(
            "scripts/ci/run-code-quality-gate.sh",
            env={
                "GITHUB_OUTPUT": output_path,
                "DOCKER_BUILD_RESULT": "success",
                "PRIMARY_LINT_RESULT": "failure",
                "PRIMARY_LINT_STATUS": "failed",
                "PRIMARY_LINT_EXIT_CODE": "1",
                "PRIMARY_LINT_TIMEOUT_FLAKE": "",
                "PRIMARY_LINT_TIMED_OUT_TOOLS": "",
            },
        )
        assert_that(result.returncode).is_equal_to(1)
        output = Path(output_path).read_text()
        assert_that(output).contains("result=failure")
        assert_that(output).contains("passed=false")
    finally:
        Path(output_path).unlink(missing_ok=True)


def test_run_gate_does_not_absorb_a_build_failure_on_timeout_evidence() -> None:
    """A docker-build failure normalizes to failed/1 and must stay red.

    ``run-code-quality-gate.sh`` scopes the timeout evidence by
    ``verdict-source``, so lint-only proof can never green a build failure.
    """
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as output_file:
        output_path = output_file.name

    try:
        result = _run_script(
            "scripts/ci/run-code-quality-gate.sh",
            env={
                "GITHUB_OUTPUT": output_path,
                "DOCKER_BUILD_RESULT": "failure",
                "PRIMARY_LINT_RESULT": "success",
                "PRIMARY_LINT_STATUS": "passed",
                "PRIMARY_LINT_EXIT_CODE": "0",
                "PRIMARY_LINT_TIMEOUT_FLAKE": "true",
                "PRIMARY_LINT_TIMED_OUT_TOOLS": "mypy",
            },
        )
        assert_that(result.returncode).is_equal_to(1)
        assert_that(Path(output_path).read_text()).contains("passed=false")
    finally:
        Path(output_path).unlink(missing_ok=True)


def test_run_gate_never_consumes_the_skip_gate_verdict() -> None:
    """Only the authoritative attempt's own outputs may reach the gate.

    ``dogfood-skip-gate`` runs its own copy of the classifier over a full-repo
    lint that is a different run from the authoritative one, so its verdict
    must stay diagnostic. The gate reads ``*_LINT_TIMEOUT_FLAKE`` from the
    reusable lint workflow only.
    """
    for name in ("run-code-quality-gate.sh", "evaluate-code-quality-gate.sh"):
        script = (_REPO_ROOT / "scripts" / "ci" / name).read_text(encoding="utf-8")
        assert_that(script).described_as(name).does_not_contain("dogfood-skip-gate")
        assert_that(script).described_as(name).does_not_contain("SKIP_GATE")


@pytest.mark.parametrize(
    ("docker_build", "expected_source"),
    [
        ("failure", "docker-build"),
        ("success", "lint"),
    ],
)
def test_evaluate_code_quality_gate_reports_verdict_source(
    *,
    docker_build: str,
    expected_source: str,
) -> None:
    """The evaluator names the job its verdict came from (#1653)."""
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as output_file:
        output_path = output_file.name

    try:
        result = _run_script(
            "scripts/ci/evaluate-code-quality-gate.sh",
            env={
                "GITHUB_OUTPUT": output_path,
                "DOCKER_BUILD_RESULT": docker_build,
                "PRIMARY_LINT_RESULT": "failure",
                "PRIMARY_LINT_STATUS": "failed",
                "PRIMARY_LINT_EXIT_CODE": "1",
            },
        )
        assert_that(result.returncode).is_equal_to(0)
        assert_that(Path(output_path).read_text()).contains(
            f"verdict-source={expected_source}",
        )
    finally:
        Path(output_path).unlink(missing_ok=True)


# --- Fail-closed required check (#2296) -------------------------------------


@pytest.mark.parametrize(
    (
        "upstream_result",
        "status_output",
        "exit_code_output",
        "expected_returncode",
        "expected_status",
        "expected_infra_flake",
    ),
    [
        # Runner shutdown: SIGTERM'd lint reported no verdict, so the required
        # check goes red while still naming the cause for the auto-rerun.
        ("failure", "", "143", 1, "status=no-verdict", "infra-flake=true"),
        # A clean lint run stays green and is not flagged as noise.
        ("success", "passed", "0", 0, "status=passed", "infra-flake=false"),
        # A genuine lint failure is red and must never claim to be infra noise.
        ("failure", "failed", "1", 1, "status=failed", "infra-flake=false"),
    ],
)
def test_assert_required_check_fail_closed_matrix(
    *,
    upstream_result: str,
    status_output: str,
    exit_code_output: str,
    expected_returncode: int,
    expected_status: str,
    expected_infra_flake: str,
) -> None:
    """The required check is red unless a lint verdict says it passed (#2296).

    Acceptance matrix for the fail-closed gate: runner loss (exit 143) is red
    with ``infra-flake=true``, a successful run is green, and a genuine lint
    failure is red with ``infra-flake=false`` so nothing reruns it as noise.
    """
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as output_file:
        output_path = output_file.name

    try:
        result = _run_script(
            "scripts/ci/assert-required-check.sh",
            env={
                "GITHUB_OUTPUT": output_path,
                "UPSTREAM_RESULT": upstream_result,
                "STATUS_OUTPUT": status_output,
                "EXIT_CODE_OUTPUT": exit_code_output,
                "STATUS_EXPECTED": "passed",
            },
        )
        assert_that(result.returncode).is_equal_to(expected_returncode)
        output = Path(output_path).read_text()
        assert_that(output).contains(expected_status)
        assert_that(output).contains(expected_infra_flake)
    finally:
        Path(output_path).unlink(missing_ok=True)


def test_assert_required_check_keeps_a_passing_verdict_green() -> None:
    """A post-lint job failure on a passing verdict is still absorbed.

    This is the mirror image of the no-verdict case and the only infra class
    #2296 leaves green: lint ran and passed, and only a later step of the
    surrounding job failed. ``infra-flake=true`` keeps image promotion blocked.
    """
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as output_file:
        output_path = output_file.name

    try:
        result = _run_script(
            "scripts/ci/assert-required-check.sh",
            env={
                "GITHUB_OUTPUT": output_path,
                "UPSTREAM_RESULT": "failure",
                "STATUS_OUTPUT": "passed",
                "EXIT_CODE_OUTPUT": "0",
            },
        )
        assert_that(result.returncode).is_equal_to(0)
        output = Path(output_path).read_text()
        assert_that(output).contains("status=passed")
        assert_that(output).contains("infra-flake=true")
    finally:
        Path(output_path).unlink(missing_ok=True)


def test_assert_required_check_fails_closed_on_a_status_only_no_verdict() -> None:
    """The status-mismatch branch fails closed too (#2296).

    An upstream job that reported success while its lint status says something
    other than ``passed`` — a SIGTERM'd run that wrote a stale status on the
    way out — has no verdict either.
    """
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as output_file:
        output_path = output_file.name

    try:
        result = _run_script(
            "scripts/ci/assert-required-check.sh",
            env={
                "GITHUB_OUTPUT": output_path,
                "UPSTREAM_RESULT": "success",
                "STATUS_OUTPUT": "failed",
                "EXIT_CODE_OUTPUT": "143",
            },
        )
        assert_that(result.returncode).is_equal_to(1)
        output = Path(output_path).read_text()
        assert_that(output).contains("status=no-verdict")
        assert_that(output).contains("infra-flake=true")
    finally:
        Path(output_path).unlink(missing_ok=True)


def test_gate_summary_explains_a_no_verdict_failure() -> None:
    """The red check must read as runner loss, not as a lint violation."""
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as summary_file:
        summary_path = summary_file.name

    try:
        result = _run_script(
            "scripts/ci/summarize-code-quality-gate.sh",
            env={
                "GITHUB_STEP_SUMMARY": summary_path,
                "GATE_INFRA_FLAKE": "true",
                "GATE_STATUS": "no-verdict",
                "GATE_RESULT": "failure",
                "GITHUB_RUN_ATTEMPT": "2",
                "MAX_RERUNS": "3",
            },
        )
        assert_that(result.returncode).is_equal_to(0)
        summary = Path(summary_path).read_text()
        assert_that(summary).contains("No lint verdict (runner loss)")
        assert_that(summary).contains("auto-rerun will retry")
        assert_that(summary).contains("run attempt 2; up to 3 automatic reruns")
    finally:
        Path(summary_path).unlink(missing_ok=True)


def test_gate_summary_is_silent_without_an_infra_flake() -> None:
    """A plain lint failure gets no runner-loss explanation."""
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as summary_file:
        summary_path = summary_file.name

    try:
        result = _run_script(
            "scripts/ci/summarize-code-quality-gate.sh",
            env={
                "GITHUB_STEP_SUMMARY": summary_path,
                "GATE_INFRA_FLAKE": "false",
                "GATE_STATUS": "failed",
                "GATE_RESULT": "failure",
            },
        )
        assert_that(result.returncode).is_equal_to(0)
        assert_that(Path(summary_path).read_text()).is_empty()
    finally:
        Path(summary_path).unlink(missing_ok=True)


def test_gate_summary_explains_an_absorbed_post_lint_failure() -> None:
    """A green infra-flake gets the promotion-blocked explanation instead."""
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as summary_file:
        summary_path = summary_file.name

    try:
        result = _run_script(
            "scripts/ci/summarize-code-quality-gate.sh",
            env={
                "GITHUB_STEP_SUMMARY": summary_path,
                "GATE_INFRA_FLAKE": "true",
                "GATE_STATUS": "passed",
                "GATE_RESULT": "success",
            },
        )
        assert_that(result.returncode).is_equal_to(0)
        summary = Path(summary_path).read_text()
        assert_that(summary).contains("Lint passed")
        assert_that(summary).does_not_contain("No lint verdict")
    finally:
        Path(summary_path).unlink(missing_ok=True)


def test_auto_rerun_signature_matches_the_assert_script_message() -> None:
    """The rerun signature is a fixed string grepped from the failed job log.

    ``auto-rerun-on-infra-failure.yml`` matches with ``grep -qF``, so the
    signature must stay a byte-identical substring of what
    ``assert-required-check.sh`` prints; a drifted message would silently stop
    the rerun. The literal below is the third copy on purpose — changing the
    message means changing all three together, and this test is what makes a
    half-done rename loud.
    """
    signature = "No lint verdict (runner loss); auto-rerun will retry"
    assert_script = (
        _REPO_ROOT / "scripts" / "ci" / "assert-required-check.sh"
    ).read_text(encoding="utf-8")
    workflow = (
        _REPO_ROOT / ".github" / "workflows" / "auto-rerun-on-infra-failure.yml"
    ).read_text(encoding="utf-8")

    assert_that(assert_script).contains(signature)
    assert_that(workflow).contains(signature)


def test_gate_summary_attempt_budget_matches_the_auto_rerun_budget() -> None:
    """The summary's "attempt N of M" must not quote a stale rerun budget.

    ``MAX_RERUNS`` in docker-ci.yml and ``max-reruns`` in
    auto-rerun-on-infra-failure.yml are the same number in two files; bind
    them so a changed budget cannot leave the job summary lying.
    """
    docker_ci = (_REPO_ROOT / ".github" / "workflows" / "docker-ci.yml").read_text(
        encoding="utf-8",
    )
    auto_rerun = (
        _REPO_ROOT / ".github" / "workflows" / "auto-rerun-on-infra-failure.yml"
    ).read_text(encoding="utf-8")

    summary_budget = re.findall(r"MAX_RERUNS: '(\d+)'", docker_ci)
    rerun_budget = re.findall(r"max-reruns: '(\d+)'", auto_rerun)

    assert_that(summary_budget).is_length(1)
    assert_that(rerun_budget).is_length(1)
    assert_that(summary_budget).is_equal_to(rerun_budget)


def test_gate_summary_stops_promising_a_rerun_past_the_budget() -> None:
    """The last attempt must not claim another rerun is coming.

    ``max-reruns`` counts reruns, not attempts, so attempt 4 of a budget of 3
    is the final one and the summary says the budget is exhausted.
    """
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as summary_file:
        summary_path = summary_file.name

    try:
        result = _run_script(
            "scripts/ci/summarize-code-quality-gate.sh",
            env={
                "GITHUB_STEP_SUMMARY": summary_path,
                "GATE_INFRA_FLAKE": "true",
                "GATE_STATUS": "no-verdict",
                "GATE_RESULT": "failure",
                "GITHUB_RUN_ATTEMPT": "4",
                "MAX_RERUNS": "3",
            },
        )
        assert_that(result.returncode).is_equal_to(0)
        summary = Path(summary_path).read_text()
        assert_that(summary).contains("budget (3) is now exhausted")
    finally:
        Path(summary_path).unlink(missing_ok=True)
