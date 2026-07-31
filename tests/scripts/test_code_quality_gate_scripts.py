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
    """Absorbing a flake must be visible to consumers via the infra-flake output."""
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
        assert_that(result.returncode).is_equal_to(0)
        assert_that(Path(output_path).read_text()).contains("infra-flake=true")
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
                "MANIFEST_SYNC_RESULT": "success",
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
                "MANIFEST_SYNC_RESULT": "success",
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
                "MANIFEST_SYNC_RESULT": "success",
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
                "MANIFEST_SYNC_RESULT": "success",
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
                "MANIFEST_SYNC_RESULT": "success",
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


def test_run_code_quality_gate_passes_after_runner_shutdown() -> None:
    """End-to-end gate should absorb a SIGTERM (exit 143) runner shutdown."""
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
                "PRIMARY_LINT_EXIT_CODE": "143",
                "RETRY_LINT_STATUS": "",
                "RETRY_LINT_EXIT_CODE": "143",
            },
        )
        assert_that(result.returncode).is_equal_to(0)
        output = Path(output_path).read_text()
        assert_that(output).contains("result=success")
        assert_that(output).contains("passed=true")
        # Absorbed noise proves nothing about lint, so publish must be blocked.
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
                "MANIFEST_SYNC_RESULT": "success",
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
                "MANIFEST_SYNC_RESULT": "success",
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
                "MANIFEST_SYNC_RESULT": "success",
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


# --- Tool-execution timeout classification (#1653) ---------------------------


def test_gate_never_absorbs_on_a_timeout_claim() -> None:
    """A tool-timeout claim must not green a lint-shaped verdict.

    #1653 originally absorbed a lint failure when the no-silent-skip gate
    reported ``timeout-flake=true``. That evidence comes from a *different*
    job which always lints the full repo, so it is not evidence about the
    authoritative run: under ``lint-scope == 'changed'`` the verdict comes from
    changed files only, and a tool that times out contributes zero findings
    precisely because it did not finish. A genuine finding could therefore be
    absorbed and the required check turned green (lgtm-ci#746).

    The absorb was removed. This asserts it stays removed: an unknown
    environment variable must not change the classification of a lint verdict.
    """
    proc = _run_script(
        "scripts/ci/is-infra-flake-failure.sh",
        env={
            "UPSTREAM_RESULT": "failure",
            "STATUS_OUTPUT": "failed",
            "EXIT_CODE_OUTPUT": "1",
            # Whatever a future caller passes, a real lint verdict stays red.
            "TIMEOUT_FLAKE": "true",
        },
    )

    assert_that(proc.returncode).is_equal_to(1)


def test_gate_scripts_carry_no_timeout_flake_plumbing() -> None:
    """No gate script may consume a tool-timeout flag.

    Guards against the absorb being reintroduced by wiring the flag back in
    (lgtm-ci#746). A sound implementation must classify the authoritative
    run's own structured report, which the upstream reusable lint workflow
    does not publish today.
    """
    for name in (
        "is-infra-flake-failure.sh",
        "assert-required-check.sh",
        "run-code-quality-gate.sh",
    ):
        script = (_REPO_ROOT / "scripts" / "ci" / name).read_text(
            encoding="utf-8",
        )
        assert_that(script).described_as(name).does_not_contain("TIMEOUT_FLAKE")


@pytest.mark.parametrize(
    ("docker_build", "manifest_sync", "expected_source"),
    [
        ("failure", "success", "docker-build"),
        ("success", "failure", "manifest-sync"),
        ("success", "success", "lint"),
        ("success", "skipped", "lint"),
    ],
)
def test_evaluate_code_quality_gate_reports_verdict_source(
    *,
    docker_build: str,
    manifest_sync: str,
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
                "MANIFEST_SYNC_RESULT": manifest_sync,
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
