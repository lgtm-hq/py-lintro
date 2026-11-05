"""Workflow wiring contracts: docker."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import pytest
import yaml
from assertpy import assert_that

from tests.integration._tools import TOOLS_IMAGE_ENV
from tests.unit.workflow_wiring_helpers import (
    _DOGFOOD_TOOL_OPTIONS_RE,
    _EVIDENCE_JOB_IF_ALLOWLIST,
    _EVIDENCE_STEP_IF_ALLOWLIST,
    _EXPECTED_DOGFOOD_TOOL_OPTIONS,
    _GITHUB_PULL_REQUEST_EVENT,
    _LINTRO_REPORT_SCRIPT,
    _NIGHTLY_IMAGE_CONSUMERS,
    _NIGHTLY_IMAGE_STEP_CONSUMERS,
    _OIDC_HOST,
    _PINNED_IMAGE_SITES,
    _PROMOTED_IMAGE_DIGESTS,
    _RELEASE_IMAGE_DIGEST_OUTPUTS,
    _RELEASE_IMAGE_JOBS,
    _REPO_ROOT,
    _SIGSTORE_HOSTS,
    _evaluate_github_if,
    _github_event_name_is_pull_request_token,
    _github_head_repo_not_fork_token,
    _github_pull_request_not_draft_token,
    _job_ancestors,
    _job_steps,
    _js_regex_to_python,
    _load_workflow,
    _normalize_github_expr,
    _regenerate_step_index,
    _renovate_pinned_image_manager,
    _tools_dockerfile_uv_version,
)


def test_docker_ci_changes_job_classifies_version_bump_prs() -> None:
    """The changes job nominates bump PRs and feeds the verdict downstream.

    Nominate-then-verify (#1362): the bump step runs only on pull_request
    events with the identity signals in env (never interpolated into the
    run script), and the resolve step consumes its output as RELEASE_BUMP.
    """
    docker_ci = _load_workflow(name="docker-ci.yml")
    changes_job = docker_ci["jobs"]["changes"]
    steps = {step.get("id"): step for step in changes_job["steps"] if "id" in step}

    bump_step = steps["bump"]
    assert_that(bump_step["if"]).contains(_github_event_name_is_pull_request_token())
    assert_that(bump_step["run"]).is_equal_to("scripts/ci/release-bump-only.sh")
    assert_that(bump_step["continue-on-error"]).is_true()
    bump_env = bump_step["env"]
    assert_that(bump_env["PR_AUTHOR"]).contains(
        f"github.event.{_GITHUB_PULL_REQUEST_EVENT}.user.login",
    )
    assert_that(bump_env["PR_TITLE"]).contains(
        f"github.event.{_GITHUB_PULL_REQUEST_EVENT}.title",
    )
    assert_that(bump_env["HEAD_REF"]).contains("github.head_ref")

    resolve_step = steps["result"]
    assert_that(resolve_step["env"]["RELEASE_BUMP"]).contains(
        "steps.bump.outputs.release-bump",
    )
    assert_that(changes_job["outputs"]["skip-reason"]).contains(
        "steps.result.outputs.skip-reason",
    )


def test_docker_ci_heavy_jobs_log_skip_reason() -> None:
    """docker-build, security-audit, and integration-test log skip notices.

    Required-check gates must report green with a logged reason when the
    pipeline is skipped (docs-only or version-bump PR, #1362).
    """
    docker_ci = _load_workflow(name="docker-ci.yml")
    for job_name in ("docker-build", "security-audit", "integration-test"):
        job = docker_ci["jobs"][job_name]
        skip_steps = [
            step
            for step in job["steps"]
            if step.get("if") == "needs.changes.outputs.pipeline == 'false'"
            and "skipped:" in step.get("run", "")
        ]
        assert_that(skip_steps).described_as(job_name).is_length(1)
        skip_step = skip_steps[0]
        # Inlined (#2297): ci-log.sh only ever ran `echo "$*"`.
        assert_that(skip_step["run"]).starts_with("echo ")
        # Double-quoted, not bare: skip reasons contain spaces ("version-bump
        # PR", "docs-only change"), so an unquoted expansion would word-split
        # the reason across echo arguments.
        assert_that(skip_step["run"]).contains('"skipped: $SKIP_REASON')
        assert_that(skip_step["env"]["SKIP_REASON"]).contains(
            "needs.changes.outputs.skip-reason",
        )


def test_docker_ci_gates_semgrep_lockfile_drift_before_the_builds() -> None:
    """The semgrep lockfile gate runs on the full-lint filter, before builds.

    #2436: nothing regenerates requirements-semgrep.txt automatically, so a
    stale lockfile has to fail one named check early instead of a dozen
    downstream jobs. Both requirements-semgrep files live in the changes
    job's ``full-lint`` path filter, which is what lint-scope reports.
    """
    docker_ci = _load_workflow(name="docker-ci.yml")
    gate = docker_ci["jobs"]["semgrep-lock"]
    condition = _normalize_github_expr(gate["if"])

    assert_that(gate["needs"]).is_equal_to(["changes"])
    assert_that(condition).contains("!cancelled()")
    assert_that(condition).contains("needs.changes.outputs.pipeline != 'false'")
    assert_that(condition).contains("needs.changes.outputs.lint-scope != 'changed'")

    steps = gate["steps"]
    assert_that(steps[0]["name"]).is_equal_to("Harden Runner")
    endpoints = steps[0]["with"]["allowed-endpoints"].split()
    assert_that(endpoints).contains("pypi.org:443", "files.pythonhosted.org:443")
    assert_that([step.get("run") for step in steps]).contains(
        "scripts/ci/check-semgrep-lock.sh",
    )

    # Only the scripts and the two requirements files are checked out.
    checkout = next(step for step in steps if step.get("name") == "Checkout")
    sparse = checkout["with"]["sparse-checkout"].split()
    assert_that(sparse).contains(
        "scripts/ci/check-semgrep-lock.sh",
        "scripts/ci/semgrep-lock-lib.sh",
        "requirements-semgrep.in",
        "requirements-semgrep.txt",
    )

    # Exact uv pin plus the retry pair (#1487): `latest` resolves through the
    # astral-sh/versions manifest, and an install flake here would skip
    # publish, which needs this job.
    assert_that(gate["env"]["UV_VERSION"]).is_equal_to(_tools_dockerfile_uv_version())
    setup_uv = [
        step for step in steps if "astral-sh/setup-uv@" in (step.get("uses") or "")
    ]
    assert_that(setup_uv).is_length(2)
    for step in setup_uv:
        assert_that(step["with"]["version"]).contains("env.UV_VERSION")
        assert_that(step["with"]["version"]).does_not_contain("latest")
    assert_that(setup_uv[0]["continue-on-error"]).is_true()
    assert_that(setup_uv[1]["if"]).contains("steps.setup-uv.outcome == 'failure'")
    assert_that(endpoints).contains("github-releases.githubusercontent.com:443")

    # The gate is upstream of the image builds, so drift is red in under a
    # minute, and upstream of publish, so a drifted lockfile never ships.
    assert_that(docker_ci["jobs"]["docker-build"]["needs"]).contains("semgrep-lock")
    assert_that(docker_ci["jobs"]["publish"]["needs"]).contains("semgrep-lock")
    # docker-build now depends on a job that is skipped on docs-only and
    # lint-scope=changed PRs, so its `!cancelled()` is load-bearing: without it
    # the required 🐳 Build Docker Images check would be skipped on those PRs
    # and merges would deadlock. publish also needs an explicit status check
    # for skipped transitive lint branches, but must require a successful
    # semgrep-lock job so a red or skipped drift gate cannot reach GHCR.
    build_condition = _normalize_github_expr(docker_ci["jobs"]["docker-build"]["if"])
    assert_that(build_condition).contains("!cancelled()")
    publish_condition = _normalize_github_expr(docker_ci["jobs"]["publish"]["if"])
    assert_that(publish_condition).contains("!cancelled()")
    assert_that(publish_condition).contains("needs.semgrep-lock.result == 'success'")
    assert_that(publish_condition).does_not_contain("always()")

    # The path filter the gate leans on still lists both lockfile paths.
    detect = next(
        step
        for step in docker_ci["jobs"]["changes"]["steps"]
        if step.get("id") == "detect"
    )
    filters = detect["with"]["filters"]
    assert_that(filters).contains("'requirements-semgrep.in'")
    assert_that(filters).contains("'requirements-semgrep.txt'")


def test_docker_ci_dogfooding_lint_waits_on_docker_build() -> None:
    """Dogfooding lint depends on the docker build (#2180: no manifest-sync)."""
    docker_ci = _load_workflow(name="docker-ci.yml")
    lint_job = docker_ci["jobs"]["dogfooding-lint"]
    comment_job = docker_ci["jobs"]["dogfooding-pr-comment"]
    lint_needs = lint_job["needs"]
    lint_condition = lint_job["if"]
    comment_condition = comment_job["if"]

    assert_that(lint_needs).contains("changes", "docker-build")
    assert_that(lint_needs).does_not_contain("manifest-sync")
    assert_that(lint_condition).contains("always()")
    assert_that(lint_condition).contains("!cancelled()")
    assert_that(lint_condition).contains("needs.changes.outputs.pipeline != 'false'")
    assert_that(lint_condition).contains("needs.docker-build.result == 'success'")
    assert_that(comment_condition).contains("always()")
    assert_that(comment_condition).contains("!cancelled()")
    assert_that(comment_condition).contains(
        "needs.dogfooding-lint.result != 'cancelled'",
    )
    assert_that(comment_condition).contains(
        "needs.dogfooding-lint.result != 'skipped'",
    )
    assert_that(comment_condition).contains(
        "needs.dogfooding-lint.outputs.exit-code != ''",
    )
    assert_that(comment_condition).contains(
        "needs.dogfooding-lint.outputs.status != ''",
    )
    assert_that(comment_condition).contains(_github_event_name_is_pull_request_token())
    assert_that(comment_condition).contains(_github_head_repo_not_fork_token())
    assert_that(comment_condition).contains(_github_pull_request_not_draft_token())


@pytest.mark.parametrize(
    (
        "pipeline",
        "lint_scope",
        "docker_build",
        "cancelled",
        "expected",
    ),
    [
        ("true", "full", "success", False, True),
        ("true", "full", "failure", False, False),
        ("true", "full", "success", True, False),
        # Changed-files PR (#1361): the full-repo lint hands off to
        # dogfooding-lint-changed.
        ("true", "changed", "success", False, False),
        # Docs-only PR: docker-build early-exits green; dogfooding-lint must
        # not run (no CI image pushed).
        ("false", "changed", "success", False, False),
        # Broken changes job fails open: pipeline and lint-scope outputs are
        # empty (not 'false'/'changed'), the full build ran, so the full
        # lint runs too.
        ("", "", "success", False, True),
    ],
)
def test_docker_ci_lint_condition_semantics(
    *,
    pipeline: str,
    lint_scope: str,
    docker_build: str,
    cancelled: bool,
    expected: bool,
) -> None:
    """Lint job ``if:`` runs only when docker-build succeeds."""
    docker_ci = _load_workflow(name="docker-ci.yml")
    lint_condition = docker_ci["jobs"]["dogfooding-lint"]["if"]

    assert_that(
        _evaluate_github_if(
            lint_condition,
            cancelled=cancelled,
            results={"docker-build": docker_build},
            outputs={"changes": {"pipeline": pipeline, "lint-scope": lint_scope}},
        ),
    ).is_equal_to(expected)


@pytest.mark.parametrize(
    (
        "pipeline",
        "lint_scope",
        "docker_build",
        "cancelled",
        "expected",
    ),
    [
        # Changed-scope PR with a green build runs the changed-files lint.
        ("true", "changed", "success", False, True),
        ("true", "changed", "failure", False, False),
        ("true", "changed", "success", True, False),
        # Full-scope runs (global-impact PRs, merge_group, pushes) belong to
        # dogfooding-lint, not this job.
        ("true", "full", "success", False, False),
        # Docs-only PR: nothing was built, nothing to lint.
        ("false", "changed", "success", False, False),
        # Broken changes job fails open to the FULL lint job: empty
        # lint-scope is != 'changed', so this job stays skipped.
        ("", "", "success", False, False),
    ],
)
def test_docker_ci_lint_changed_condition_semantics(
    *,
    pipeline: str,
    lint_scope: str,
    docker_build: str,
    cancelled: bool,
    expected: bool,
) -> None:
    """Changed-files lint runs exactly when scope is 'changed' and build is green."""
    docker_ci = _load_workflow(name="docker-ci.yml")
    lint_condition = docker_ci["jobs"]["dogfooding-lint-changed"]["if"]

    assert_that(
        _evaluate_github_if(
            lint_condition,
            cancelled=cancelled,
            results={"docker-build": docker_build},
            outputs={"changes": {"pipeline": pipeline, "lint-scope": lint_scope}},
        ),
    ).is_equal_to(expected)


@pytest.mark.parametrize(
    (
        "dogfooding_lint",
        "cancelled",
        "event_is_pull_request",
        "head_repo_not_fork",
        "pull_request_not_draft",
        "lint_outputs",
        "expected",
    ),
    [
        (
            "success",
            False,
            True,
            True,
            True,
            {"exit-code": "0", "status": "passed"},
            True,
        ),
        (
            "failure",
            False,
            True,
            True,
            True,
            {"exit-code": "1", "status": "failed"},
            True,
        ),
        (
            "skipped",
            False,
            True,
            True,
            True,
            {"exit-code": "0", "status": "passed"},
            False,
        ),
        (
            "cancelled",
            False,
            True,
            True,
            True,
            {"exit-code": "0", "status": "passed"},
            False,
        ),
        (
            "success",
            True,
            True,
            True,
            True,
            {"exit-code": "0", "status": "passed"},
            False,
        ),
        (
            "success",
            False,
            False,
            True,
            True,
            {"exit-code": "0", "status": "passed"},
            False,
        ),
        (
            "success",
            False,
            True,
            False,
            True,
            {"exit-code": "0", "status": "passed"},
            False,
        ),
        (
            "success",
            False,
            True,
            True,
            False,
            {"exit-code": "0", "status": "passed"},
            False,
        ),
        (
            "failure",
            False,
            True,
            True,
            True,
            {"exit-code": "", "status": "failed"},
            False,
        ),
        ("failure", False, True, True, True, {"exit-code": "1", "status": ""}, False),
    ],
)
def test_docker_ci_comment_condition_semantics(
    *,
    dogfooding_lint: str,
    cancelled: bool,
    event_is_pull_request: bool,
    head_repo_not_fork: bool,
    pull_request_not_draft: bool,
    lint_outputs: dict[str, str],
    expected: bool,
) -> None:
    """PR comment job respects lint results, outputs, and PR safety guards."""
    docker_ci = _load_workflow(name="docker-ci.yml")
    comment_condition = docker_ci["jobs"]["dogfooding-pr-comment"]["if"]

    assert_that(
        _evaluate_github_if(
            comment_condition,
            cancelled=cancelled,
            results={
                "dogfooding-lint": dogfooding_lint,
                # Full-scope scenarios: neither the changed-files job nor the
                # bounded retry ran, so the full lint result decides.
                "dogfooding-lint-changed": "skipped",
                "dogfooding_lint_retry": "skipped",
            },
            outputs={
                "dogfooding-lint": lint_outputs,
                "dogfooding-lint-changed": {"exit-code": "", "status": ""},
                "dogfooding_lint_retry": {"exit-code": "", "status": ""},
            },
            event_is_pull_request=event_is_pull_request,
            head_repo_not_fork=head_repo_not_fork,
            pull_request_not_draft=pull_request_not_draft,
        ),
    ).is_equal_to(expected)


@pytest.mark.parametrize(
    ("lint_changed", "changed_outputs", "expected"),
    [
        # Changed-files lint ran: the comment posts with its outputs even
        # though the full lint job was scope-skipped.
        ("success", {"exit-code": "0", "status": "passed"}, True),
        ("failure", {"exit-code": "1", "status": "failed"}, True),
        ("skipped", {"exit-code": "", "status": ""}, False),
        ("failure", {"exit-code": "", "status": ""}, False),
    ],
)
def test_docker_ci_comment_condition_changed_scope_semantics(
    *,
    lint_changed: str,
    changed_outputs: dict[str, str],
    expected: bool,
) -> None:
    """PR comment fires on changed-files lint results when full lint skipped."""
    docker_ci = _load_workflow(name="docker-ci.yml")
    comment_condition = docker_ci["jobs"]["dogfooding-pr-comment"]["if"]

    assert_that(
        _evaluate_github_if(
            comment_condition,
            cancelled=False,
            results={
                "dogfooding-lint": "skipped",
                "dogfooding-lint-changed": lint_changed,
                # Retry only ever runs for the full-repo lint; in changed scope
                # the full job was skipped, so the retry is skipped too.
                "dogfooding_lint_retry": "skipped",
            },
            outputs={
                "dogfooding-lint": {"exit-code": "", "status": ""},
                "dogfooding-lint-changed": changed_outputs,
                "dogfooding_lint_retry": {"exit-code": "", "status": ""},
            },
            event_is_pull_request=True,
            head_repo_not_fork=True,
            pull_request_not_draft=True,
        ),
    ).is_equal_to(expected)


def test_docker_ci_lintro_code_quality_wires_upstream_jobs() -> None:
    """Required check consumes the code-quality gate rollup output."""
    docker_ci = _load_workflow(name="docker-ci.yml")
    gate_job = docker_ci["jobs"]["code-quality-gate"]
    job = docker_ci["jobs"]["lintro-code-quality"]

    # The gate rolls up the effective dogfooding result across the full run,
    # its retry, and the changed-files variant (#1313 + #1361).
    assert_that(gate_job["needs"]).contains(
        "changes",
        "docker-build",
        "dogfooding-lint",
        "dogfooding-lint-changed",
        "dogfooding_lint_retry",
    )
    # The gate's primary lint selection mirrors whichever dogfooding job ran:
    # docs-only PRs report success, lint-scope 'changed' selects the
    # changed-files job, anything else selects the full run.
    gate_step = next(step for step in gate_job["steps"] if step.get("id") == "gate")
    primary_result = _normalize_github_expr(gate_step["env"]["PRIMARY_LINT_RESULT"])
    assert_that(primary_result).is_equal_to(
        _normalize_github_expr(
            "${{ needs.changes.outputs.pipeline == 'false' && 'success' "
            "|| needs.changes.outputs.lint-scope == 'changed' && "
            "needs.dogfooding-lint-changed.result "
            "|| needs.dogfooding-lint.result }}",
        ),
    )
    assert_that(gate_step["env"]["RETRY_LINT_RESULT"]).contains(
        "needs.dogfooding_lint_retry.result",
    )
    # The required check consumes only the rolled-up gate outputs.
    assert_that(job["needs"]).contains("code-quality-gate")
    assert_that(job["if"]).contains("!cancelled()")
    assert_that(job["with"]["upstream-result"]).contains(
        "needs.code-quality-gate.outputs.result",
    )
    assert_that(job["with"]["passed-output"]).contains(
        "needs.code-quality-gate.outputs.passed",
    )
    assert_that(job["with"]["status-output"]).contains(
        "needs.code-quality-gate.outputs.status",
    )


def test_docker_ci_publish_refuses_absorbed_infra_flake() -> None:
    """An absorbed infra flake keeps the check green but must not publish."""
    docker_ci = _load_workflow(name="docker-ci.yml")
    gate_job = docker_ci["jobs"]["code-quality-gate"]
    publish = docker_ci["jobs"]["publish"]

    assert_that(gate_job["outputs"]).contains_key("infra-flake")
    assert_that(publish["needs"]).contains("code-quality-gate")
    publish_if = _normalize_github_expr(publish["if"])
    assert_that(publish_if).contains(
        "needs.code-quality-gate.outputs.result == 'success'",
    )
    assert_that(publish_if).contains(
        "needs.code-quality-gate.outputs.infra-flake != 'true'",
    )


def test_docker_ci_publish_handles_skipped_transitive_dependencies() -> None:
    """A successful gate must publish despite an unused lint retry branch."""
    publish = _load_workflow(name="docker-ci.yml")["jobs"]["publish"]
    condition = _normalize_github_expr(publish["if"])

    # Override Actions' implicit success() across the mutually exclusive lint
    # jobs, without allowing any failed or skipped direct prerequisite.
    assert_that(condition).contains("!cancelled()")
    for job_id in publish["needs"]:
        assert_that(condition).contains(f"needs.{job_id}.result == 'success'")


@pytest.mark.parametrize("target", ["full", "base"])
def test_docker_ci_published_image_labels_identify_app_source(target: str) -> None:
    """Digest promotion cannot replace revision labels inherited from tools.

    Args:
        target: Published application image build target.
    """
    builds = _load_workflow(name="docker-ci.yml")["jobs"]["docker-build"]["steps"]
    build = next(
        step["with"]
        for step in builds
        if step.get("with", {}).get("target") == target
        and step.get("with", {}).get("push") is True
    )
    assert_that(build.get("labels", "")).contains(
        "org.opencontainers.image.revision=${{ github.sha }}",
    )


def test_docker_ci_publish_skips_docs_only_pushes() -> None:
    """Docs-only pushes have no CI image to promote, so publish must not run."""
    docker_ci = _load_workflow(name="docker-ci.yml")
    publish = docker_ci["jobs"]["publish"]

    # The gate reports success on docs-only runs because it is a required
    # check, so publish can no longer rely on dogfooding-lint being skipped.
    assert_that(publish["needs"]).contains("changes")
    assert_that(_normalize_github_expr(publish["if"])).contains(
        "needs.changes.outputs.pipeline != 'false'",
    )


def test_docker_ci_retries_dogfooding_lint_on_failure() -> None:
    """Dogfooding lint retry runs only after a primary lint failure."""
    docker_ci = _load_workflow(name="docker-ci.yml")
    retry_job = docker_ci["jobs"]["dogfooding_lint_retry"]
    retry_condition = retry_job["if"]

    assert_that(retry_job["needs"]).contains(
        "docker-build",
        "dogfooding-lint",
    )
    assert_that(retry_condition).contains("needs.dogfooding-lint.result == 'failure'")
    assert_that(retry_condition).contains("needs.docker-build.result == 'success'")


@pytest.mark.parametrize("job_id", ["dogfooding-lint", "dogfooding_lint_retry"])
def test_dogfood_lint_callers_carry_no_githubapp_grant(job_id: str) -> None:
    """Dogfood lint callers must not allowlist githubapp.com (#2352, #2488).

    harden-runner v2.21.0 block mode denied `hosted-compute-watchdog-*` and
    `hosted-compute-request-orchestrator-*` under githubapp.com and long jobs
    were reclaimed mid-run with exit 143, so #2353 added a `*.githubapp.com:443`
    grant. v2.21.1 allows those hosts from GitHub meta on its own, so the grant
    is redundant and must not come back. The reusable workflow's enforcing
    harden-runner step reads `allowed-endpoints` verbatim, so the caller must
    still carry the whole baseline — asserting a couple of baseline hosts
    keeps a future edit from shrinking the list to one entry.

    Args:
        job_id: Dogfooding lint caller whose egress allowlist is under test.
    """
    docker_ci = _load_workflow(name="docker-ci.yml")
    job_with = docker_ci["jobs"][job_id]["with"]
    allowed = set(str(job_with["allowed-endpoints"]).split())

    assert_that(job_with["egress-policy"]).is_equal_to("audit")
    assert_that(job_with["allowed-endpoints-mode"]).is_equal_to("append")
    for endpoint in allowed:
        assert_that(endpoint.lower()).described_as(endpoint).does_not_contain(
            "githubapp.com",
        )
    assert_that(allowed).contains(
        "github.com:443",
        "api.github.com:443",
        "ghcr.io:443",
        "pypi.org:443",
    )


@pytest.mark.parametrize("workflow_name", ["docker-ci.yml", "dogfood-nightly.yml"])
def test_dogfood_callers_disable_semgrep_version_network_check(
    workflow_name: str,
) -> None:
    """Every reusable lint caller disables the startup update request (#2521)."""
    workflow = _load_workflow(name=workflow_name)
    callers = {
        job_id: job
        for job_id, job in workflow["jobs"].items()
        if "reusable-quality-lint.yml@" in str(job.get("uses", ""))
    }
    expected = (
        {"dogfooding-lint", "dogfooding_lint_retry"}
        if workflow_name == "docker-ci.yml"
        else {"dogfood-full", "dogfood_full_retry"}
    )
    assert_that(set(callers)).is_equal_to(expected)
    for job_id, job in callers.items():
        # Reusable jobs accept inputs, not env. The pinned reusable transports
        # this input through its lint step and docker run -e to Semgrep.
        assert_that(job["with"].get("semgrep-enable-version-check")).described_as(
            f"{workflow_name}:{job_id}",
        ).is_equal_to("0")
        assert_that(job).does_not_contain_key("env")


def test_dogfood_lint_callers_share_one_egress_allowlist() -> None:
    """Both dogfood lint callers must carry the identical allowlist (#2352).

    The reusable workflow's enforcing harden-runner step reads the caller's
    `allowed-endpoints` verbatim, so each caller repeats the whole baseline.
    Two hand-maintained copies drift silently — a host added for the primary
    and forgotten on the retry fails only on the retry, during an incident.
    """
    docker_ci = _load_workflow(name="docker-ci.yml")
    primary = str(docker_ci["jobs"]["dogfooding-lint"]["with"]["allowed-endpoints"])
    retry = str(
        docker_ci["jobs"]["dogfooding_lint_retry"]["with"]["allowed-endpoints"],
    )

    assert_that(primary.split()).is_equal_to(retry.split())
    assert_that(primary.split()).does_not_contain_duplicates()


@pytest.mark.parametrize(
    "job_id",
    [
        "docker-build",
        "dogfooding-lint-changed",
        "dogfood-skip-gate",
        "security-audit",
        "integration-test",
        "publish",
    ],
)
def test_long_docker_ci_jobs_carry_no_githubapp_grant(job_id: str) -> None:
    """No long in-repo Docker CI job allowlists githubapp.com (#2352, #2488).

    Jobs whose budget exceeds ~10 minutes were the ones observed dying with
    "The runner has received a shutdown signal" while harden-runner v2.21.0
    blocked GitHub's hosted-compute watchdog and request-orchestrator hosts.
    v2.21.1 allows `*.githubapp.com` from GitHub meta itself, so the grants
    #2353 added are redundant and stay out.

    Args:
        job_id: Docker CI job whose harden-runner allowlist is under test.
    """
    docker_ci = _load_workflow(name="docker-ci.yml")
    job = docker_ci["jobs"][job_id]
    harden = next(
        step
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("step-security/harden-runner@")
    )
    allowed = set(str(harden["with"]["allowed-endpoints"]).split())

    assert_that(harden["with"]["egress-policy"]).is_equal_to("audit")
    assert_that(job["timeout-minutes"]).is_greater_than(10)
    # Keep a positive floor so the guard cannot be satisfied by emptying the list.
    assert_that(allowed).contains("github.com:443", "api.github.com:443")
    for endpoint in allowed:
        assert_that(endpoint.lower()).described_as(endpoint).does_not_contain(
            "githubapp.com",
        )


def test_docker_ci_dogfood_skip_gate_can_reach_artifact_results_service() -> None:
    """The authoritative-report download needs artifact API egress (#2963)."""
    docker_ci = _load_workflow(name="docker-ci.yml")
    gate = docker_ci["jobs"]["dogfood-skip-gate"]
    harden = next(
        step
        for step in gate["steps"]
        if str(step.get("uses", "")).startswith("step-security/harden-runner@")
    )
    allowed = set(str(harden["with"]["allowed-endpoints"]).split())

    assert_that(harden["with"]["egress-policy"]).is_equal_to("audit")
    assert_that(allowed).contains(
        "pipelines.actions.githubusercontent.com:443",
        "results-receiver.actions.githubusercontent.com:443",
    )


def test_docker_ci_dogfood_skip_gate_consumes_authoritative_lint_report() -> None:
    """Full-repo skip checks wait for retry and consume its final report."""
    docker_ci = _load_workflow(name="docker-ci.yml")
    gate = docker_ci["jobs"]["dogfood-skip-gate"]

    assert_that(gate["needs"]).contains(
        "changes",
        "docker-build",
        "dogfooding-lint",
        "dogfooding-lint-changed",
        "dogfooding_lint_retry",
    )

    download = next(
        step
        for step in gate["steps"]
        if step.get("name") == "Download authoritative lint JSON report"
    )
    assert_that(download["if"]).contains(
        "needs.changes.outputs.lint-scope != 'changed'",
    )
    assert_that(download["uses"]).contains(
        "actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333",
    )
    assert_that(download["with"]).is_equal_to(
        {"name": "linting-json-report", "path": ".lintro/artifacts/json"},
    )

    check = next(step for step in gate["steps"] if step.get("id") == "skips")
    report_expr = _normalize_github_expr(check["env"]["REPORT_JSON"])
    assert_that(report_expr).contains(
        "needs.changes.outputs.lint-scope != 'changed'",
    )
    assert_that(report_expr).contains(".lintro/artifacts/json/results.json")
    assert_that(report_expr).contains("|| ''")


def test_dogfood_skip_gate_has_bounded_timeout() -> None:
    """The no-silent-skip gate must fail predictably on a stall (#1704).

    The gate's hosted runner can be terminated mid-run with no diagnostic
    signal. A bounded ``timeout-minutes`` gives healthy runs room to complete
    while still failing a stalled run fast, keeping the failure mode
    predictable.
    Applies to both copies of the gate (docker-ci.yml and
    dogfood-nightly.yml); the owner-approved value is 30.
    """
    for workflow_name in ("docker-ci.yml", "dogfood-nightly.yml"):
        workflow = _load_workflow(name=workflow_name)
        gate = workflow["jobs"]["dogfood-skip-gate"]
        assert_that(gate["name"]).is_equal_to("🚦 Dogfood No-Silent-Skip Gate")
        timeout = gate.get("timeout-minutes")
        assert_that(
            timeout,
            f"{workflow_name} dogfood-skip-gate must set timeout-minutes",
        ).is_not_none()
        # Provides headroom for healthy runs while bounding stalled runs.
        assert_that(timeout).is_equal_to(30)


def test_tools_image_switch_is_declared_where_the_suite_runs() -> None:
    """The Docker side declares the exact variable the gate reads (#465).

    ``LINTRO_TOOLS_IMAGE`` is what turns a missing wrapped tool from a skip
    into a failure. Its name lives in Python as
    ``tests.integration._tools.TOOLS_IMAGE_ENV`` but has to be repeated as a
    plain string in the Dockerfile and in docker-compose.yml, which neither
    can import. A rename on one side would silently degrade the required
    Docker integration check back to a rubber stamp, so pin all three.
    """
    switch = f"{TOOLS_IMAGE_ENV}=1"

    dockerfile = (_REPO_ROOT / "docker" / "tools.Dockerfile").read_text(
        encoding="utf-8",
    )
    # Match the ENV instruction itself: a bare substring would also be
    # satisfied by the surrounding comment or by a RUN line, neither of which
    # puts the variable in the test process's environment.
    env_instruction = re.search(
        rf"(?m)^\s*ENV\s+{re.escape(switch)}(?:\s|$)",
        dockerfile,
    )
    assert_that(env_instruction).described_as(
        "docker/tools.Dockerfile ENV instruction",
    ).is_not_none()

    compose = yaml.safe_load(
        (_REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"),
    )
    environment = compose["services"]["test-integration"]["environment"]
    assert_that(environment).described_as("test-integration service").contains(switch)

    # The hosted matrix is the other half of the lockstep: it runs the same
    # modules on a toolless runner, so copying the switch onto the reusable
    # would turn every absent wrapped tool into a collection failure there.
    test_ci = _load_workflow(name="test-ci.yml")
    for job_name in ("test-compat", "test-coverage"):
        job_text = yaml.safe_dump(test_ci["jobs"][job_name])
        assert_that(job_text).described_as(job_name).does_not_contain(
            TOOLS_IMAGE_ENV,
        )


def test_docker_ci_detect_step_has_no_pipeline_allow_list() -> None:
    """The detect-changes filter feeds lint-scope only, never pipeline.

    Reintroducing a `pipeline:` dorny filter would resurrect the rotting
    allow-list that #1369 removed; relevance must stay computed by
    resolve-pipeline-relevance.sh from the changed-file list.
    """
    docker_ci = _load_workflow(name="docker-ci.yml")
    changes_job = docker_ci["jobs"]["changes"]
    steps = {step.get("id"): step for step in changes_job["steps"] if "id" in step}

    detect_step = steps["detect"]
    filters = detect_step["with"]["filters"]
    filter_names = re.findall(r"^([^#\s][^:]*):\s*$", filters, re.M)
    assert_that(filter_names).is_equal_to(["full-lint"])
    assert_that(filters).contains("- 'justfile'")
    assert_that(filters).does_not_contain("- 'Makefile'")

    resolve_step = steps["result"]
    assert_that(resolve_step["run"]).is_equal_to(
        "scripts/ci/resolve-pipeline-relevance.sh",
    )
    # The script diffs the merge commit (HEAD^1..HEAD): the changes job
    # checkout must keep full history for that range to resolve.
    checkout_steps = [
        step
        for step in changes_job["steps"]
        if "actions/checkout" in step.get("uses", "")
    ]
    assert_that(checkout_steps).is_length(1)
    assert_that(checkout_steps[0]["with"]["fetch-depth"]).is_equal_to(0)


def test_lintro_report_runs_full_codebase_analysis_exactly_once() -> None:
    """The scheduled report must run the heavy ``lintro check .`` analysis once.

    Running the full-codebase analysis twice (once for the artifact and once for
    the step summary) doubled peak memory and OOM-killed the 7GB runner. The
    generation script must invoke the analysis a single time and reuse its
    markdown output for both the report artifact and the step summary.
    """
    script = _LINTRO_REPORT_SCRIPT.read_text(encoding="utf-8")

    # The only heavy invocation is the Docker ``lintro check .`` run. Match the
    # actual command (via the ``DOCKER_RUN`` array) so comments/echo text that
    # merely mention "lintro check" are not counted.
    analysis_runs = re.findall(
        r'"\$\{DOCKER_RUN\[@\]\}"\s+lintro\s+check\b',
        script,
    )
    assert_that(analysis_runs).is_length(1)

    # The step summary must reuse the already-written report rather than trigger
    # a second analysis. ``list-tools`` is lightweight and allowed.
    assert_that(script).contains("tail -n +5 lintro-report/report.md")
    assert_that(script).contains("--output-format markdown")


def test_lintro_report_scheduled_workflow_shares_single_run_output() -> None:
    """The scheduled workflow wires one analysis step to the report artifact."""
    workflow = _load_workflow(name="lintro-report-scheduled.yml")
    report_job = workflow["jobs"]["lintro-report"]
    assert_that(report_job["timeout-minutes"]).is_equal_to(30)

    # Concurrency guard for the report ref must remain intact.
    assert_that(report_job["concurrency"]["group"]).is_equal_to(
        "report-${{ github.ref }}",
    )
    assert_that(report_job["concurrency"]["cancel-in-progress"]).is_true()

    steps = report_job["steps"]
    generate_steps = [
        step
        for step in steps
        if "lintro-report-generate.sh" in str(step.get("run", ""))
    ]
    assert_that(generate_steps).is_length(1)

    upload_steps = [
        step
        for step in steps
        if str(step.get("uses", "")).startswith("actions/upload-artifact")
    ]
    assert_that(upload_steps).is_length(1)
    assert_that(upload_steps[0]["with"]["path"]).is_equal_to(
        "lintro-report/report.md",
    )

    # The notify job consumes the report job's result, not a second analysis.
    # GitHub Actions accepts both scalar and list forms for `needs`.
    notify_needs = workflow["jobs"]["notify"]["needs"]
    if isinstance(notify_needs, str):
        notify_needs = [notify_needs]
    assert_that(notify_needs).contains("lintro-report")


def test_docker_ci_regenerates_manifest_before_verify() -> None:
    """The manifest gate regenerates the artifacts before verifying (#2179).

    Once the artifacts stop being committed (epic #2176 phase 4), a missing
    or reordered regeneration step would only fail in live CI; this locks
    the wiring at unit-test time.
    """
    docker_ci = _load_workflow(name="docker-ci.yml")
    steps = docker_ci["jobs"]["integration-test"]["steps"]
    regen_index = _regenerate_step_index(steps)
    verify_index = next(
        index
        for index, step in enumerate(steps)
        if step.get("run") == "scripts/ci/verify-image-manifest-tools.sh"
    )
    assert_that(regen_index).is_less_than(verify_index)


def test_dogfood_nightly_regenerates_manifest_before_verify() -> None:
    """The nightly pinned-digest gate regenerates before verifying (#2179)."""
    nightly = _load_workflow(name="dogfood-nightly.yml")
    steps = nightly["jobs"]["verify-pinned-image-tools"]["steps"]
    regen_index = _regenerate_step_index(steps)
    verify_index = next(
        index
        for index, step in enumerate(steps)
        if step.get("run") == "scripts/ci/verify-image-manifest-tools.sh"
    )
    assert_that(regen_index).is_less_than(verify_index)


def test_docker_ci_integration_verifies_ci_image_tools() -> None:
    """integration-test runs the manifest-vs-image gate on the built CI image."""
    docker_ci = _load_workflow(name="docker-ci.yml")
    steps = docker_ci["jobs"]["integration-test"]["steps"]
    verify_steps = [
        step
        for step in steps
        if step.get("run") == "scripts/ci/verify-image-manifest-tools.sh"
    ]
    assert_that(verify_steps).is_length(1)
    verify = verify_steps[0]
    # Gated like the other heavy steps so docs-only PRs still report green.
    assert_that(verify["if"]).is_equal_to("needs.changes.outputs.pipeline != 'false'")
    # The CI image is retagged py-lintro:latest by both the GHCR pull and the
    # fork tarball load, so forks gate on their own built image.
    assert_that(verify["env"]["IMAGE"]).is_equal_to("py-lintro:latest")


def test_docker_ci_integration_passes_base_ref_for_version_lag() -> None:
    """integration-test passes BASE_REF so runtime version-lag matches the gate."""
    docker_ci = _load_workflow(name="docker-ci.yml")
    steps = docker_ci["jobs"]["integration-test"]["steps"]
    run_steps = [
        step
        for step in steps
        if step.get("run") == "scripts/docker/run-docker-test-suite.sh"
    ]
    assert_that(run_steps).is_length(1)
    # docker-test.sh uses BASE_REF to populate LINTRO_ALLOW_VERSION_LAG from
    # compute-new-manifest-tools.sh (EMIT=version-changed), mirroring #1582.
    assert_that(run_steps[0]["env"]["BASE_REF"]).is_equal_to("${{ github.base_ref }}")


def test_dogfood_nightly_gates_pinned_digest_tools() -> None:
    """dogfood-nightly verifies the resolved image and notifies on failure."""
    nightly = _load_workflow(name="dogfood-nightly.yml")
    jobs = nightly["jobs"]
    assert_that(jobs).contains_key("verify-pinned-image-tools")

    verify_job = jobs["verify-pinned-image-tools"]
    assert_that(verify_job["needs"]).contains("resolve-image")
    verify_steps = [
        step
        for step in verify_job["steps"]
        if step.get("run") == "scripts/ci/verify-image-manifest-tools.sh"
    ]
    assert_that(verify_steps).is_length(1)
    # The same image dogfood-full lints with, resolved once per run (#2602).
    assert_that(verify_steps[0]["env"]["IMAGE"]).is_equal_to(
        "${{ needs.resolve-image.outputs.image }}",
    )
    # And checked out at the commit that image was built from, so the gate
    # reports genuine image-vs-manifest drift instead of pin lag.
    checkout = next(
        step
        for step in verify_job["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert_that(checkout["with"]["ref"]).is_equal_to(
        "${{ needs.resolve-image.outputs.manifest-ref }}",
    )

    # A pinned-digest failure must still reach the deduplicated failure
    # notifier — now through the classifier that gates it (#2246).
    assert_that(jobs["notify-failure"]["needs"]).contains("classify-failure")
    assert_that(jobs["classify-failure"]["needs"]).contains(
        "verify-pinned-image-tools",
    )


def test_dogfood_nightly_carries_no_hard_coded_release_pin() -> None:
    """No pinned release literal may return to dogfood-nightly.yml (#2602).

    A pin here is only ever correct on the day it is written: the Renovate
    docker datasource cannot page past the package's thousands of
    ``sha-<commit>`` tags to find a newer release, and the release-time sync
    script reaches CI without a token (lgtm-hq/lgtm-ci#849). So the literal
    is banned outright rather than trusted to stay fresh.
    """
    text = (_REPO_ROOT / ".github" / "workflows" / "dogfood-nightly.yml").read_text(
        encoding="utf-8",
    )

    assert_that(
        re.findall(r"py-lintro:\d+\.\d+\.\d+@sha256:[a-f0-9]{64}", text),
    ).is_empty()
    # Not even a bare digest: resolution is the resolver job's job, and an
    # image digest anywhere else in this file is a pin wearing a disguise.
    assert_that(re.findall(r"py-lintro@?:?sha256:[a-f0-9]{64}", text)).is_empty()


def test_dogfood_nightly_resolves_its_image_once() -> None:
    """Every nightly image consumer reads the one resolved reference (#2602).

    Five copies of one literal is how the pin went stale; five copies of one
    expression would at least stay coherent, but a consumer that kept its own
    literal would silently lint a different image than the gate verifies. Each
    consumer must therefore read ``resolve-image`` and declare the dependency
    that makes the ``needs`` context available to it.
    """
    nightly = _load_workflow(name="dogfood-nightly.yml")
    jobs = nightly["jobs"]
    resolver = jobs["resolve-image"]

    # Digest, version and the manifest ref all come from the one step, so a
    # consumer can never pair one run's image with another run's manifest.
    for output, key in (
        ("image", "image"),
        ("version", "version"),
        ("manifest-ref", "manifest-ref"),
    ):
        assert_that(resolver["outputs"][output]).is_equal_to(
            "${{ steps.resolve.outputs." + key + " }}",
        )
    resolve_step = next(
        step for step in resolver["steps"] if step.get("id") == "resolve"
    )
    assert_that(resolve_step["run"]).contains("scripts/ci/resolve-image-digest.sh")
    # The commit this run checked out is what the resolver prefers an image
    # for; without it the preferred per-commit path cannot even be attempted.
    assert_that(resolve_step["env"]["COMMIT_SHA"]).is_equal_to("${{ github.sha }}")

    expression = "${{ needs.resolve-image.outputs.image }}"
    for job_id in _NIGHTLY_IMAGE_CONSUMERS:
        job = jobs[job_id]
        assert_that(job["needs"]).described_as(job_id).contains("resolve-image")
        assert_that(job["with"]["lintro-image"]).described_as(job_id).is_equal_to(
            expression,
        )
    for job_id in _NIGHTLY_IMAGE_STEP_CONSUMERS:
        job = jobs[job_id]
        assert_that(job["needs"]).described_as(job_id).contains("resolve-image")
        images = [
            step["env"]["LINTRO_IMAGE"]
            for step in job["steps"]
            if "LINTRO_IMAGE" in (step.get("env") or {})
        ]
        assert_that(images).described_as(job_id).is_equal_to([expression])


def test_dogfood_nightly_resolver_script_is_executable() -> None:
    """The resolver the nightly invokes must exist and be runnable (#2602)."""
    script = _REPO_ROOT / "scripts" / "ci" / "resolve-image-digest.sh"

    assert_that(script.is_file()).is_true()
    assert_that(os.access(script, os.X_OK)).is_true()


def test_dogfood_nightly_classifier_sees_the_resolver() -> None:
    """A resolver failure must reach the tracker, not vanish (#2602).

    When ``resolve-image`` fails there is no image, so every lint job below it
    is skipped and the night produces no coverage at all. Without the
    resolver in the classifier's inputs that reads as "nothing failed" and the
    tracker never hears about the gap.
    """
    nightly = _load_workflow(name="dogfood-nightly.yml")
    classify = nightly["jobs"]["classify-failure"]

    assert_that(classify["needs"]).contains("resolve-image")
    step = next(step for step in classify["steps"] if step.get("id") == "classify")
    assert_that(step["env"]["RESOLVE_RESULT"]).is_equal_to(
        "${{ needs.resolve-image.result }}",
    )


def test_docker_ci_defers_ci_tag_cleanup() -> None:
    """docker-ci must not delete run-scoped CI tags on completion (#1138).

    Immediate cleanup (even when gated on no-failure) still races partial
    reruns and leaves "Re-run failed jobs" as a trap. Tags stay
    ``ci-${{ github.run_id }}``; age-based reclaim is owned by ghcr-cleanup.
    """
    docker_ci = _load_workflow(name="docker-ci.yml")
    jobs = docker_ci["jobs"]
    assert_that(jobs).does_not_contain_key("cleanup-ci-images")

    inline_cleanup = [
        step.get("run", "")
        for job in jobs.values()
        for step in (job.get("steps") or [])
        if isinstance(step, dict) and step.get("run")
    ]
    assert_that(inline_cleanup).does_not_contain(
        "scripts/ci/maintenance/delete-ci-ghcr-tags.sh",
    )
    assert_that(inline_cleanup).does_not_contain(
        "scripts/ci/maintenance/sweep-ci-ghcr-tags.sh",
    )

    # Tag scheme stays run-scoped (not attempt-scoped); build still pushes it.
    build_steps = jobs["docker-build"]["steps"]
    tag_values = [
        (step.get("with") or {}).get("tags", "")
        for step in build_steps
        if isinstance(step, dict)
    ]
    assert_that("\n".join(tag_values)).contains("ci-${{ github.run_id }}")
    assert_that("\n".join(tag_values)).does_not_contain("github.run_attempt")


def test_docker_ci_runs_main_in_parallel_and_serialises_only_publish() -> None:
    """Main runs must not queue behind each other; promotions still must.

    A workflow-level ``queue: max`` on main turned ~40-minute runs into a
    multi-hour backlog that timed out the all-checks gate. Main runs now get
    a group of their own (pinned by the run-scoped contract test), the build
    job carries no per-ref group that would re-serialise them, and only the
    GHCR promotion keeps a mutex — with ``queue: max`` so a third main run
    cannot replace a waiting promotion.
    """
    docker_ci = _load_workflow(name="docker-ci.yml")
    workflow_concurrency = docker_ci["concurrency"]
    assert_that(workflow_concurrency).does_not_contain_key("queue")
    assert_that(
        _normalize_github_expr(workflow_concurrency["cancel-in-progress"]),
    ).is_equal_to("${{ github.event_name == 'pull_request' }}")

    assert_that(docker_ci["jobs"]["docker-build"]).does_not_contain_key(
        "concurrency",
    )

    publish_concurrency = docker_ci["jobs"]["publish"]["concurrency"]
    assert_that(publish_concurrency).is_equal_to(
        {
            "group": "publish-${{ github.ref }}",
            "queue": "max",
            "cancel-in-progress": False,
        },
    )


def test_docker_ci_only_current_main_tip_updates_rolling_image_tags() -> None:
    """Stale queued runs keep immutable SHA tags but cannot move branch tags."""
    docker_ci = _load_workflow(name="docker-ci.yml")
    steps = docker_ci["jobs"]["publish"]["steps"]
    rolling_index, rolling = next(
        (index, step)
        for index, step in enumerate(steps)
        if step.get("id") == "rolling-tags"
    )
    assert_that(rolling["run"]).is_equal_to(
        "scripts/ci/resolve-docker-rolling-tags.sh",
    )
    assert_that(rolling["env"]).is_equal_to(
        {
            "DEFAULT_BRANCH": "${{ github.event.repository.default_branch }}",
            "RUN_SHA": "${{ github.sha }}",
        },
    )

    metadata_steps = [
        (index, step)
        for index, step in enumerate(steps)
        if step.get("uses", "").startswith("docker/metadata-action@")
    ]
    assert_that(metadata_steps).is_length(2)
    for metadata_index, step in metadata_steps:
        assert_that(rolling_index).is_less_than(metadata_index)
        tag_lines = {line.strip() for line in step["with"]["tags"].splitlines()}
        assert_that(tag_lines).contains(
            "type=ref,event=branch,enable=${{ "
            "steps.rolling-tags.outputs.rolling-tags-enabled }}",
        )
        assert_that(tag_lines).contains("type=sha,prefix=sha-,format=long")


def test_pinned_release_image_sites_share_one_reference() -> None:
    """Every pinned py-lintro release site must name the same release.

    The docker-ci fork-PR fallback pins a released ``py-lintro`` image by
    digest, because a fork build is never pushed to GHCR and has no image of
    its own to lint with. Renovate bumps every site as one set (#1751), and a
    partial bump would leave consumers linting with different images while all
    claim to use "the pinned release" — this asserts that cannot happen.

    The pattern is the one Renovate itself is configured with, so a pin that
    is reworded out of the manager's reach fails here rather than silently
    dropping out of coverage.
    """
    match_string = _renovate_pinned_image_manager()["matchStrings"][0]
    pattern = re.compile(_js_regex_to_python(match_string))

    references: set[str] = set()
    for filename, expected_sites in _PINNED_IMAGE_SITES.items():
        workflow = _REPO_ROOT / ".github" / "workflows" / filename
        assert_that(workflow.is_file()).is_true()
        matches = pattern.findall(workflow.read_text(encoding="utf-8"))
        # Per-file count, so deleting pins from one workflow cannot hide
        # behind pins that remain in the other.
        assert_that(matches).described_as(filename).is_length(expected_sites)
        references.update(matches)

    assert_that(references).is_length(1)


def test_docker_ci_fork_fallback_resolves_through_one_env() -> None:
    """Every docker-ci fork-fallback consumer reads the one workflow-level pin.

    The pin used to be copy-pasted at four consumers, which is how it drifted
    four releases behind the published image (#2297). It now lives once in
    ``env.LINTRO_FORK_FALLBACK_IMAGE``. Two consumers read that context
    directly; the two reusable-workflow callers cannot, because ``env`` is not
    an available context in ``jobs.<id>.with`` — they go through
    ``needs.docker-build.outputs.fork-fallback-image``, which republishes the
    same env. This asserts no consumer reverted to its own literal and that
    every caller still declares the ``docker-build`` dependency that
    indirection needs.
    """
    docker_ci = _load_workflow(name="docker-ci.yml")
    pin = docker_ci["env"]["LINTRO_FORK_FALLBACK_IMAGE"].strip()

    assert_that(pin).matches(
        r"^ghcr\.io/lgtm-hq/py-lintro:\d+\.\d+\.\d+@sha256:[a-f0-9]{64}$",
    )
    # Published from a step, not interpolated straight from `env`, so the
    # output can never be empty — an empty middle operand is falsy in the
    # consumers' ternary and would silently select a never-pushed ci- tag.
    build = docker_ci["jobs"]["docker-build"]
    assert_that(build["outputs"]).contains_entry(
        {"fork-fallback-image": "${{ steps.fork-fallback.outputs.image }}"},
    )
    publish_steps = [
        step for step in build["steps"] if step.get("id") == "fork-fallback"
    ]
    assert_that(publish_steps).is_length(1)
    publish = publish_steps[0]
    # The exact write, not just a mention of the variable: a step that merely
    # logged the pin, or wrote it under a different key, would leave the
    # output empty — and empty is falsy in the consumers' ternary, silently
    # selecting a ci- tag that fork runs never push.
    assert_that(publish["run"].strip()).is_equal_to(
        'echo "image=$LINTRO_FORK_FALLBACK_IMAGE" >> "$GITHUB_OUTPUT"',
    )
    # The key written above must be the key the job output reads back.
    assert_that(build["outputs"]["fork-fallback-image"]).contains(
        f"steps.{publish['id']}.outputs.image",
    )
    # Unconditional: the output must exist for every event, not just the ones
    # that reach the heavy build steps.
    assert_that(publish).does_not_contain_key("if")

    # job id -> the expression carrying the fork-fallback selection.
    reusable_callers = ("dogfooding-lint", "dogfooding_lint_retry")
    step_consumers = {
        "dogfooding-lint-changed": "LINTRO_IMAGE",
        "dogfood-skip-gate": "LINTRO_IMAGE",
    }
    expressions: list[str] = []
    # Reusable callers must go through the job output and must NOT use `env`:
    # `env` is not an available context in `jobs.<id>.with`, so accepting it
    # here would let an invalid workflow pass this test.
    for job_id in reusable_callers:
        job = docker_ci["jobs"][job_id]
        assert_that(job["needs"]).contains("docker-build")
        expression = job["with"]["lintro-image"]
        assert_that(expression).described_as(job_id).contains(
            "needs.docker-build.outputs.fork-fallback-image",
        )
        assert_that(expression).described_as(job_id).does_not_contain(
            "env.LINTRO_FORK_FALLBACK_IMAGE",
        )
        expressions.append(expression)
    # Step-level consumers read the workflow env directly.
    for job_id, env_key in step_consumers.items():
        job = docker_ci["jobs"][job_id]
        assert_that(job["needs"]).contains("docker-build")
        values = [
            step["env"][env_key]
            for step in job["steps"]
            if env_key in (step.get("env") or {})
        ]
        assert_that(values).described_as(job_id).is_length(1)
        assert_that(values[0]).described_as(job_id).contains(
            "env.LINTRO_FORK_FALLBACK_IMAGE",
        )
        expressions.append(values[0])

    assert_that(expressions).is_length(4)
    for expression in expressions:
        # No consumer may carry its own literal digest again.
        assert_that(expression).described_as(expression).does_not_contain("sha256:")
        # The fork-vs-same-repo selection the pin exists for must survive,
        # including its polarity: the pin is the `&&` branch (fork) and the
        # run-scoped CI tag the `||` branch (same repo). Asserting only that
        # both fragments appear would accept an inverted ternary, which would
        # hand fork PRs a ci- tag that fork runs never push.
        collapsed = " ".join(expression.split())
        condition = "needs.docker-build.outputs.is-fork == 'true'"
        ci_tag = "format('ghcr.io/lgtm-hq/py-lintro:ci-{0}', github.run_id)"
        assert_that(collapsed).described_as(expression).contains(condition)
        assert_that(collapsed).described_as(expression).contains(ci_tag)
        pin_token = next(
            token
            for token in (
                "needs.docker-build.outputs.fork-fallback-image",
                "env.LINTRO_FORK_FALLBACK_IMAGE",
            )
            if token in collapsed
        )
        # condition ... && <pin> ... || <ci tag>
        assert_that(collapsed.index(condition)).described_as(
            expression,
        ).is_less_than(collapsed.index(pin_token))
        assert_that(collapsed.index(pin_token)).described_as(
            expression,
        ).is_less_than(collapsed.index("||"))
        assert_that(collapsed.index("||")).described_as(
            expression,
        ).is_less_than(collapsed.index(ci_tag))


def test_pinned_release_image_manager_covers_both_workflows() -> None:
    """The custom manager must still target every pinned-image workflow.

    Without it the pin is a manual multi-site edit that only ever moves when
    something breaks (#1751) — the failure mode behind #1590, where the pinned
    image lagged the manifest by seven tool versions and one absent binary.
    """
    manager = _renovate_pinned_image_manager()

    assert_that(manager.get("datasourceTemplate")).is_equal_to("docker")

    # Renovate matches file patterns against repo-relative paths; assert each
    # workflow this repo pins in is actually reachable by one of them.
    file_patterns = [
        re.compile(_js_regex_to_python(pattern.strip("/")))
        for pattern in manager.get("managerFilePatterns", [])
    ]
    for filename in _PINNED_IMAGE_SITES:
        path = f".github/workflows/{filename}"
        covered = any(pattern.search(path) for pattern in file_patterns)
        assert_that(covered).described_as(path).is_true()


# --- Tool-execution timeout classification wiring (#1653) --------------------


def test_dogfood_skip_gate_publishes_timeout_flake_outputs() -> None:
    """The no-silent-skip gate must publish its timeout verdict as job outputs."""
    docker_ci = _load_workflow(name="docker-ci.yml")
    gate = docker_ci["jobs"]["dogfood-skip-gate"]

    outputs = gate.get("outputs") or {}
    assert_that(outputs).contains_key("timeout-flake")
    assert_that(_normalize_github_expr(outputs["timeout-flake"])).contains(
        "steps.skips.outputs.timeout-flake",
    )

    step_ids = [step.get("id") for step in gate["steps"]]
    assert_that(step_ids).contains("skips")


def test_code_quality_gate_ignores_the_skip_gate_diagnostic_verdict() -> None:
    """The skip gate's own timeout verdict must never reach the gate.

    ``dogfood-skip-gate`` always lints the full repo, so its verdict is not
    evidence about the authoritative lint run: under ``lint-scope ==
    'changed'`` that run lints only changed files, and a tool that times out
    reports zero findings precisely because it did not finish. Wiring that
    verdict into the gate lets a genuine finding be absorbed.

    The gate consumes the reusable lint workflow's outputs instead, which are
    computed from the authoritative run's own report (#2242, lgtm-ci#746).
    """
    docker_ci = _load_workflow(name="docker-ci.yml")
    gate_job = docker_ci["jobs"]["code-quality-gate"]

    assert_that(gate_job["needs"]).does_not_contain("dogfood-skip-gate")

    gate_step = next(step for step in gate_job["steps"] if step.get("id") == "gate")
    env = gate_step.get("env") or {}
    for value in env.values():
        assert_that(_normalize_github_expr(str(value))).does_not_contain(
            "dogfood-skip-gate",
        )


def test_code_quality_gate_consumes_authoritative_timeout_outputs() -> None:
    """The gate reads timeout evidence from the authoritative lint attempts.

    Both attempts that call ``reusable-quality-lint.yml`` expose
    ``timeout-flake`` / ``timed-out-tools`` from their own JSON report, and
    the gate script pairs each with the verdict of the same attempt (#2242).
    """
    docker_ci = _load_workflow(name="docker-ci.yml")
    gate_step = next(
        step
        for step in docker_ci["jobs"]["code-quality-gate"]["steps"]
        if step.get("id") == "gate"
    )
    env = gate_step["env"]

    for key in (
        "PRIMARY_LINT_TIMEOUT_FLAKE",
        "PRIMARY_LINT_TIMED_OUT_TOOLS",
        "RETRY_LINT_TIMEOUT_FLAKE",
        "RETRY_LINT_TIMED_OUT_TOOLS",
    ):
        assert_that(env).contains_key(key)

    assert_that(_normalize_github_expr(env["PRIMARY_LINT_TIMEOUT_FLAKE"])).contains(
        "needs.dogfooding-lint.outputs.timeout-flake",
    )
    assert_that(_normalize_github_expr(env["RETRY_LINT_TIMEOUT_FLAKE"])).contains(
        "needs.dogfooding_lint_retry.outputs.timeout-flake",
    )


def test_code_quality_gate_leaves_changed_scope_timeout_fail_closed() -> None:
    """Changed scope must resolve the timeout flag to the empty string.

    ``dogfooding-lint-changed`` is a local job with no JSON report and no
    timeout verdict, so the expression is inverted relative to the
    status/exit-code pairs: ``''`` is falsy in a GitHub ternary and a
    ``scope == 'changed' && '' || <full>`` form would leak the full run's
    value into changed scope.
    """
    docker_ci = _load_workflow(name="docker-ci.yml")
    gate_step = next(
        step
        for step in docker_ci["jobs"]["code-quality-gate"]["steps"]
        if step.get("id") == "gate"
    )
    changed_job_outputs = docker_ci["jobs"]["dogfooding-lint-changed"]["outputs"]
    assert_that(changed_job_outputs).does_not_contain_key("timeout-flake")

    for key in ("PRIMARY_LINT_TIMEOUT_FLAKE", "PRIMARY_LINT_TIMED_OUT_TOOLS"):
        expression = _normalize_github_expr(gate_step["env"][key])
        assert_that(expression).contains(
            "needs.changes.outputs.lint-scope != 'changed'",
        )
        assert_that(expression).does_not_contain("dogfooding-lint-changed")
        assert_that(expression).ends_with("|| '' }}")


def test_lint_timeout_classifier_note_matches_the_wired_gate() -> None:
    """The classifier's scope warning must not claim the gate ignores it.

    The lgtm-ci#746 precondition is met at the pinned ``v0.63.7``, so the
    stale "the code-quality gate therefore does not consume it" note was
    replaced by the narrower, still-true statement about the skip gate.
    """
    classifier = (_REPO_ROOT / "scripts" / "ci" / "classify-lint-timeout.py").read_text(
        encoding="utf-8",
    )
    assert_that(classifier).does_not_contain(
        "The code-quality gate therefore does not consume it",
    )
    assert_that(classifier).contains("reusable lint workflow")


def test_dogfood_skip_gate_checks_out_the_timeout_classifier() -> None:
    """The skip gate script must be able to reach the classifier it calls."""
    script = (_REPO_ROOT / "scripts" / "ci" / "dogfood-skip-gate.sh").read_text(
        encoding="utf-8",
    )
    assert_that(script).contains("classify-lint-timeout.py")
    classifier = _REPO_ROOT / "scripts" / "ci" / "classify-lint-timeout.py"
    assert_that(classifier.exists()).is_true()


def test_code_quality_gate_sparse_checkout_covers_gate_scripts() -> None:
    """Every script run by the gate job must be in its sparse checkout."""
    docker_ci = _load_workflow(name="docker-ci.yml")
    gate_job = docker_ci["jobs"]["code-quality-gate"]
    checkout = next(
        step
        for step in gate_job["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout")
    )
    sparse = checkout["with"]["sparse-checkout"]

    for script in (
        "scripts/ci/run-code-quality-gate.sh",
        "scripts/ci/evaluate-code-quality-gate.sh",
        "scripts/ci/assert-required-check.sh",
        "scripts/ci/is-infra-flake-failure.sh",
        "scripts/ci/summarize-code-quality-gate.sh",
    ):
        assert_that(sparse).contains(script)


def test_code_quality_gate_explains_an_infra_flake_in_the_summary() -> None:
    """A fail-closed gate must say whether the red was runner loss (#2296).

    The summary step has to survive the gate step's own failure, so it is
    guarded by ``always()`` — that failure is exactly what it explains.
    """
    docker_ci = _load_workflow(name="docker-ci.yml")
    gate_job = docker_ci["jobs"]["code-quality-gate"]

    summary_step = next(
        step
        for step in gate_job["steps"]
        if "summarize-code-quality-gate.sh" in str(step.get("run", ""))
    )
    assert_that(str(summary_step["run"]).strip()).is_equal_to(
        "scripts/ci/summarize-code-quality-gate.sh",
    )

    condition = _normalize_github_expr(str(summary_step["if"]))
    assert_that(condition).contains("always()")
    assert_that(condition).contains("steps.gate.outputs.infra-flake == 'true'")

    # The script branches on GATE_STATUS and quotes MAX_RERUNS, and refuses to
    # write anything unless GATE_INFRA_FLAKE is the literal 'true'. A missing
    # key would silently fall back to a default and print the wrong story.
    env = summary_step["env"]
    assert_that(_normalize_github_expr(str(env["GATE_INFRA_FLAKE"]))).is_equal_to(
        "${{ steps.gate.outputs.infra-flake }}",
    )
    assert_that(_normalize_github_expr(str(env["GATE_STATUS"]))).is_equal_to(
        "${{ steps.gate.outputs.status }}",
    )
    assert_that(str(env["MAX_RERUNS"])).is_equal_to("3")


def test_docker_image_jobs_share_one_allowed_endpoints_source() -> None:
    """The three image jobs must not carry their own copies of the allowlist.

    Three verbatim copies drifted apart silently: a host added for one target
    was easy to forget on the other two, and under ``replace`` semantics a
    missing baseline host fails the build only during a release (#1821).
    """
    workflow = _load_workflow(name="docker-build-publish.yml")
    jobs = workflow["jobs"]
    expected = "${{ needs.resolve-endpoints.outputs.endpoints }}"

    for job_name in ("docker-base", "docker-full", "docker-ai"):
        job = jobs[job_name]
        assert_that(job["with"]["allowed-endpoints"]).is_equal_to(expected)
        assert_that(job["with"]["allowed-endpoints-mode"]).is_equal_to("replace")
        assert_that(job["needs"]).contains("resolve-endpoints")
        # An empty output would blank the allowlist under replace semantics and
        # block all egress, so the image jobs must not start without it.
        assert_that(_normalize_github_expr(job["if"])).contains(
            "needs.resolve-endpoints.result == 'success'",
        )


def test_resolve_endpoints_job_publishes_the_shared_allowlist() -> None:
    """The resolver job must read the checked-in allowlist and export it.

    GitHub Actions rejects YAML anchors and forbids the ``env`` context in
    job-level ``with:`` blocks of reusable-workflow calls, so a job output is
    the only way the three callers can share one list (#1821).
    """
    workflow = _load_workflow(name="docker-build-publish.yml")
    job = workflow["jobs"]["resolve-endpoints"]

    assert_that(job["outputs"]["endpoints"]).is_equal_to(
        "${{ steps.resolve.outputs.endpoints }}",
    )
    resolve_steps = [
        step
        for step in job["steps"]
        if step.get("run") == "./scripts/ci/resolve-allowed-endpoints.sh"
    ]
    assert_that(resolve_steps).is_length(1)
    assert_that(resolve_steps[0]["id"]).is_equal_to("resolve")
    assert_that(resolve_steps[0]["env"]["ENDPOINTS_FILE"]).is_equal_to(
        ".github/allowed-endpoints/docker-build-publish.txt",
    )
    assert_that((_REPO_ROOT / resolve_steps[0]["env"]["ENDPOINTS_FILE"]).exists())


def test_docker_allowlist_file_keeps_the_baseline_and_signing_hosts() -> None:
    """The shared allowlist must stay a superset of the docker preset baseline.

    ``allowed-endpoints-mode: replace`` means the list is passed verbatim to
    harden-runner, so dropping a registry or sigstore host silently breaks
    pushes or cosign signing on the next release only.
    """
    allowlist = (
        _REPO_ROOT / ".github" / "allowed-endpoints" / "docker-build-publish.txt"
    ).read_text(encoding="utf-8")
    endpoints = [
        line.split("#", 1)[0].strip()
        for line in allowlist.splitlines()
        if line.split("#", 1)[0].strip()
    ]

    assert_that(endpoints).does_not_contain_duplicates()
    for endpoint in (
        "ghcr.io:443",
        "registry-1.docker.io:443",
        "auth.docker.io:443",
        "pypi.org:443",
        "files.pythonhosted.org:443",
        # The full signing set, shared with the attesting-jobs test so the
        # resolver-fed image jobs are held to the same hosts as every
        # literal replace-mode list (#2562).
        *sorted(_SIGSTORE_HOSTS),
        _OIDC_HOST,
    ):
        assert_that(endpoints).contains(endpoint)


def test_dogfood_tool_options_are_identical_and_give_gitleaks_a_timeout() -> None:
    """Every dogfood options string must match and include ``gitleaks:timeout=120``.

    ``dogfooding-lint`` (docker-ci) and ``dogfood-full`` (dogfood-nightly),
    their bounded retries and both skip gates share one
    ``lintro-tool-options`` / ``TOOL_OPTIONS`` value. A drift lets one job keep
    the 60s gitleaks default (#2206) while the others get the 120s floor.
    """
    texts = [
        (_REPO_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        for name in ("docker-ci.yml", "dogfood-nightly.yml")
    ]
    found = [
        match.group(0)
        for text in texts
        for match in _DOGFOOD_TOOL_OPTIONS_RE.finditer(text)
    ]

    assert_that(found).is_length(8)
    assert_that(set(found)).is_equal_to({_EXPECTED_DOGFOOD_TOOL_OPTIONS})
    assert_that(_EXPECTED_DOGFOOD_TOOL_OPTIONS).contains("gitleaks:timeout=120")


def test_tools_publish_no_cache_covers_schedule_and_force_publish() -> None:
    """Weekly and force-publish rebuilds must bypass the registry cache.

    A cache hit on ``force_publish`` republishes stale tool binaries even
    after installer pin fixes (#2220, #2221). Keep the expression folded
    so yamllint ``line-length`` (88) stays green in dogfood.
    """
    path = _REPO_ROOT / ".github" / "workflows" / "docker-tools-publish.yml"
    workflow = _load_workflow(name="docker-tools-publish.yml")
    no_cache = _normalize_github_expr(
        str(workflow["jobs"]["tools-image"]["with"]["no-cache"]),
    )

    assert_that(no_cache).contains("github.event_name == 'schedule'")
    assert_that(no_cache).contains(
        "github.event_name == 'workflow_dispatch' && inputs.force_publish == 'true'",
    )

    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        if "no-cache" not in line and "force_publish" not in line:
            continue
        assert_that(len(line)).described_as(
            f"{path.name}:{lineno} exceeds yamllint line-length 88",
        ).is_less_than_or_equal_to(88)


def test_dogfood_nightly_retries_killed_lint_without_a_verdict() -> None:
    """The nightly full lint gets one bounded retry on a no-verdict kill (#2246).

    Mirrors ``dogfooding_lint_retry`` in docker-ci.yml, with one extra guard:
    a primary attempt that published a genuine verdict is never re-run, so a
    real regression reaches the tracker the same night instead of paying for
    a second twenty-minute lint that can only agree.
    """
    nightly = _load_workflow(name="dogfood-nightly.yml")
    retry = nightly["jobs"]["dogfood_full_retry"]
    condition = _normalize_github_expr(retry["if"])

    assert_that(retry["needs"]).contains("dogfood-full")
    assert_that(condition).contains("needs.dogfood-full.result == 'failure'")
    assert_that(condition).contains("needs.dogfood-full.result == 'cancelled'")
    # Runner shutdown makes lintro exit 143; that stays retryable even when a
    # dying run wrote status=failed on its way out.
    assert_that(condition).contains("needs.dogfood-full.outputs.exit-code == '143'")
    # A tool-execution timeout publishes status=failed/exit-code=1 from the
    # attempt's own report; the shared classifier calls that infra, so the
    # retry must run for it or the classifier would fail closed and ping.
    assert_that(condition).contains(
        "needs.dogfood-full.outputs.timeout-flake == 'true'",
    )
    assert_that(condition).contains("needs.dogfood-full.outputs.status != 'failed'")
    assert_that(condition).contains("needs.dogfood-full.outputs.exit-code != '1'")

    # Exactly one attempt: the retry must not depend on itself or chain.
    assert_that(nightly["jobs"]).does_not_contain_key("dogfood_full_retry_retry")
    assert_that(retry["with"]["job-name"]).contains("retry")


def test_dogfood_nightly_skip_gate_publishes_and_retries_its_verdict() -> None:
    """The nightly skip gate publishes a verdict and retries when it has none.

    The gate's ``status``/``exit-code`` outputs come from
    ``scripts/ci/dogfood-skip-gate.sh`` and exist only once the skip check
    completes, so their absence is what distinguishes a runner kill from a
    real non-allowlisted skip (#2246).
    """
    nightly = _load_workflow(name="dogfood-nightly.yml")
    gate = nightly["jobs"]["dogfood-skip-gate"]
    retry = nightly["jobs"]["dogfood_skip_gate_retry"]

    for job in (gate, retry):
        assert_that(job["outputs"]["status"]).is_equal_to(
            "${{ steps.skips.outputs.status }}",
        )
        assert_that(job["outputs"]["exit-code"]).is_equal_to(
            "${{ steps.skips.outputs.exit-code }}",
        )
        check = next(step for step in job["steps"] if step.get("id") == "skips")
        assert_that(check["run"]).contains("scripts/ci/dogfood-skip-gate.sh")

    condition = _normalize_github_expr(retry["if"])
    assert_that(retry["needs"]).contains("dogfood-skip-gate")
    assert_that(condition).contains("needs.dogfood-skip-gate.result == 'failure'")
    assert_that(condition).contains("needs.dogfood-skip-gate.result == 'cancelled'")
    # Only a gate that never reached a verdict is retried.
    assert_that(condition).contains("needs.dogfood-skip-gate.outputs.status == ''")


def test_dogfood_nightly_skip_gate_retry_is_a_lockstep_copy() -> None:
    """The skip-gate retry must run exactly what the primary ran (#2246).

    GitHub has no job-level retry, so the retry job is a copy of the primary;
    every input that decides what the gate checks (egress allowlist, image,
    timeout budget, the gate script invocation) must stay identical or the
    retry answers a different question than the attempt it retries.
    """
    nightly = _load_workflow(name="dogfood-nightly.yml")
    gate = nightly["jobs"]["dogfood-skip-gate"]
    retry = nightly["jobs"]["dogfood_skip_gate_retry"]

    def _facts(job: dict[str, Any]) -> dict[str, Any]:
        harden = next(
            step
            for step in job["steps"]
            if str(step.get("uses", "")).startswith("step-security/harden-runner@")
        )
        check = next(step for step in job["steps"] if step.get("id") == "skips")
        return {
            "timeout-minutes": job.get("timeout-minutes"),
            "allowed-endpoints": harden["with"]["allowed-endpoints"].split(),
            "egress-policy": harden["with"].get("egress-policy"),
            "image": (check.get("env") or {}).get("LINTRO_IMAGE"),
            "run": check["run"],
        }

    assert_that(_facts(retry)).is_equal_to(_facts(gate))


def test_dogfood_nightly_classifies_before_pinging_the_tracker() -> None:
    """notify-failure consumes the effective post-retry verdict (#2246).

    The tracker is a human triage queue, so the notifier must key off the
    classifier's decision rather than ``failure()``, and the classifier must
    see every job that can fail plus its retry.
    """
    nightly = _load_workflow(name="dogfood-nightly.yml")
    classify = nightly["jobs"]["classify-failure"]
    notify = nightly["jobs"]["notify-failure"]

    assert_that(classify["needs"]).contains(
        "dogfood-full",
        "dogfood_full_retry",
        "verify-pinned-image-tools",
        "dogfood-skip-gate",
        "dogfood_skip_gate_retry",
    )
    step = next(step for step in classify["steps"] if step.get("id") == "classify")
    assert_that(step["run"]).contains(
        "scripts/ci/classify-nightly-dogfood-failure.py",
    )
    # Both attempts of both retried jobs must reach the classifier.
    env = step["env"]
    for prefix in ("LINT", "LINT_RETRY"):
        for suffix in (
            "RESULT",
            "STATUS",
            "EXIT_CODE",
            "TIMEOUT_FLAKE",
            "TIMED_OUT_TOOLS",
        ):
            assert_that(env).contains_key(f"{prefix}_{suffix}")
    for prefix in ("SKIP_GATE", "SKIP_GATE_RETRY"):
        for suffix in ("RESULT", "STATUS", "EXIT_CODE"):
            assert_that(env).contains_key(f"{prefix}_{suffix}")
    assert_that(env).contains_key("VERIFY_RESULT")
    # The classifier reuses the PR path's signatures instead of copying them.
    checkout = next(
        item
        for item in classify["steps"]
        if "sparse-checkout" in (item.get("with") or {})
    )
    assert_that(checkout["with"]["sparse-checkout"]).contains(
        "scripts/ci/is-infra-flake-failure.sh",
    )

    condition = _normalize_github_expr(notify["if"])
    assert_that(notify["needs"]).is_equal_to(["classify-failure"])
    assert_that(condition).contains("github.ref == 'refs/heads/main'")
    assert_that(condition).contains("needs.classify-failure.outputs.notify == 'true'")
    # Fail closed: a classifier that did not succeed still pings.
    assert_that(condition).contains("needs.classify-failure.result != 'success'")


def test_tools_promote_fallback_preserves_native_runners() -> None:
    """Explicit mappings keep newer tooling's amd64 default from changing CI."""
    workflow = _load_workflow(name="docker-tools-promote.yml")
    with_block = workflow["jobs"]["publish-fallback"]["with"]
    assert_that(json.loads(with_block["runner-map"])).is_equal_to(
        {"linux/amd64": "ubuntu-24.04", "linux/arm64": "ubuntu-24.04-arm"},
    )


def test_tools_promote_passes_manifest_staleness_shas() -> None:
    """The promote step must feed the staleness guard both commits (#2497).

    A candidate image built before a tool manifest change landed on main must
    not be retagged as ``:latest``. The guard inside
    ``scripts/ci/promote-ci-docker-images.sh`` compares the candidate's build
    commit with main, so the workflow has to pass both SHAs and check out
    enough history for that comparison.
    """
    workflow = _load_workflow(name="docker-tools-promote.yml")
    resolve = workflow["jobs"]["resolve"]
    assert_that(resolve["outputs"]).contains_key("candidate-sha")
    assert_that(resolve["outputs"]["candidate-sha"]).contains(
        "steps.candidate.outputs.candidate-sha",
    )
    # The tag's SHA is abbreviated; the PR number is what makes it fetchable.
    assert_that(resolve["outputs"]).contains_key("candidate-pr")
    assert_that(resolve["outputs"]["candidate-pr"]).contains(
        "steps.candidate.outputs.candidate-pr",
    )

    promote = workflow["jobs"]["promote"]
    checkout = next(
        step
        for step in promote["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    # fetch-depth: 0 - the guard walks main back to the candidate branch point.
    assert_that(checkout["with"]["fetch-depth"]).is_equal_to(0)

    step = next(
        step
        for step in promote["steps"]
        if "scripts/ci/promote-ci-docker-images.sh" in str(step.get("run", ""))
    )
    env = step["env"]
    assert_that(env["CANDIDATE_SHA"]).contains("needs.resolve.outputs.candidate-sha")
    assert_that(env["CANDIDATE_PR"]).contains("needs.resolve.outputs.candidate-pr")
    assert_that(env["MAIN_SHA"]).contains("github.sha")
    # The guard's escape hatch has to be reachable: the refusal message tells
    # operators to force the promote, so a dispatch must be able to set it.
    assert_that(env["FORCE_PUBLISH"]).contains("inputs.force_publish")
    assert_that(env["EXPECTED_DIGEST"]).contains("steps.pin.outputs.digest")
    assert_that(str(env["TAGS"])).contains("lintro-tools:latest")
    assert_that(str(env["TAGS"])).contains("lintro-tools:pinned-")
    assert_that(str(env["TAGS"])).contains("steps.pin.outputs.sha7")
    dispatch = workflow["on"]["workflow_dispatch"]
    assert_that(dispatch["inputs"]).contains_key("force_publish")

    promote_script = (
        _REPO_ROOT / "scripts" / "ci" / "promote-ci-docker-images.sh"
    ).read_text(encoding="utf-8")
    assert_that(promote_script).contains("check-tools-manifest-staleness.sh")


def test_docker_ci_changed_scope_publishes_the_lint_json_report() -> None:
    """The changed-files lint job uploads the same JSON report the full run does.

    The AI review downloads ``linting-json-report`` for the PR head (#2571);
    the reusable full-repo lint already publishes it, so changed scope must
    too, and lintro only emits the file when it sees ``GITHUB_ACTIONS=true``
    inside the container, which the script forwards.
    """
    docker_ci = _load_workflow(name="docker-ci.yml")
    steps = docker_ci["jobs"]["dogfooding-lint-changed"]["steps"]
    uploads = [
        step
        for step in steps
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
        and step.get("with", {}).get("name") == "linting-json-report"
    ]
    assert_that(uploads).is_length(1)
    upload = uploads[0]
    assert_that(upload["with"]["path"]).is_equal_to(
        ".lintro/artifacts/json/results.json",
    )
    assert_that(upload["if"]).is_equal_to("always()")
    assert_that(upload.get("continue-on-error")).is_true()
    script = (_REPO_ROOT / "scripts" / "ci" / "dogfood-changed-files.sh").read_text(
        encoding="utf-8",
    )
    assert_that(script).contains("-e GITHUB_ACTIONS=true")


def test_docker_ci_evidence_steps_cannot_be_execution_bypassed() -> None:
    """The CI build/publish path cannot skip or swallow its evidence (#2630).

    CodeRabbit on #2640: the attestation assertions inspect ``with`` values
    and attestation inputs, which pass even when the producing step never
    ran — a constant-false ``if`` skips it, and ``continue-on-error: true``
    carries an attestation or signature failure to a green job. The evidence
    steps (the pushed CI-tag builds, the cosign signature, the two
    attestations) and their jobs may carry no ``continue-on-error``, and
    every ``if``, job-level and step-level, must match its allowlist
    verbatim: a job guard like ``false && always()`` is not constant-false
    yet still skips every evidence step.
    """
    ci = _load_workflow(name="docker-ci.yml")
    for job_id in ("docker-build", "publish"):
        job = ci["jobs"][job_id]
        assert_that(job.get("continue-on-error")).described_as(
            f"docker-ci.yml::{job_id} continue-on-error",
        ).is_none()
        key = f"docker-ci.yml::{job_id}"
        assert_that(key).described_as("evidence job allowlist").is_in(
            *_EVIDENCE_JOB_IF_ALLOWLIST,
        )
        assert_that(
            _normalize_github_expr(str(job.get("if", ""))),
        ).described_as(f"{key} if").is_equal_to(
            _normalize_github_expr(_EVIDENCE_JOB_IF_ALLOWLIST[key]),
        )
    evidence_steps = [
        step
        for job_id in ("docker-build", "publish")
        for step in ci["jobs"][job_id]["steps"]
        if (
            "build-push-action" in str(step.get("uses", ""))
            and (step.get("with") or {}).get("push") is True
        )
        or "cosign" in str(step.get("run", ""))
        or "attest-build-provenance" in str(step.get("uses", ""))
    ]
    assert_that(evidence_steps).described_as("evidence steps found").is_length(5)
    for step in evidence_steps:
        name = str(step.get("name", ""))
        assert_that(step.get("continue-on-error")).described_as(
            f"{name} continue-on-error",
        ).is_none()
        assert_that(
            _normalize_github_expr(str(step.get("if", ""))),
        ).described_as(f"{name} if").is_equal_to(
            _normalize_github_expr(_EVIDENCE_STEP_IF_ALLOWLIST.get(name, "")),
        )


@pytest.mark.parametrize("job_id", _RELEASE_IMAGE_JOBS)
def test_release_images_preserve_native_runners(job_id: str) -> None:
    """Explicit runner mappings preserve release builds across tooling bumps.

    Args:
        job_id: Base, full or AI release image job.
    """
    job = _load_workflow(name="docker-build-publish.yml")["jobs"][job_id]
    assert_that(json.loads(job["with"]["runner-map"])).is_equal_to(
        {"linux/amd64": "ubuntu-24.04", "linux/arm64": "ubuntu-24.04-arm"},
    )


@pytest.mark.parametrize("job_id", _RELEASE_IMAGE_JOBS)
def test_release_images_select_the_verified_trivy_repair(job_id: str) -> None:
    """All release image callers select the repaired workflow and tooling (#3200).

    Args:
        job_id: Base, full or AI release image job.
    """
    repair = "cade5e687630ce3208bf914d0b1590145f3d569e"  # lgtm-ci v0.83.11
    job = _load_workflow(name="docker-build-publish.yml")["jobs"][job_id]
    assert_that(job["uses"]).is_equal_to(
        f"lgtm-hq/lgtm-ci/.github/workflows/reusable-docker.yml@{repair}",
    )
    assert_that(job["with"]["tooling-ref"]).is_equal_to(repair)


def test_docker_build_runs_before_the_gate_in_staging_mode() -> None:
    """Images are built and attested off classify-tag, never upstream of pypi-upload.

    The staging call carries only ``staging: true``: no version, no latest, no
    prerelease gate (prereleases prove the build stage), and a ``ref_type``
    guard so a workflow_dispatch from a branch never pushes staging images.
    The old rebuild-on-tag ``docker-publish`` call is gone (#2562).
    """
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    assert_that(publish["jobs"]).does_not_contain_key("docker-publish")
    build = publish["jobs"]["docker-build"]
    assert_that(build["needs"]).is_equal_to(["classify-tag"])
    assert_that(build["uses"]).is_equal_to(
        "./.github/workflows/docker-build-publish.yml",
    )
    assert_that(build["with"]).is_equal_to({"staging": True})
    build_if = _normalize_github_expr(str(build["if"]))
    assert_that(build_if).contains("github.ref_type == 'tag'")
    assert_that(build_if).does_not_contain("is_prerelease")
    assert_that(_job_ancestors(publish, job_id="docker-build")).is_equal_to(
        {"classify-tag"},
    )


def test_docker_promote_depends_on_the_github_release() -> None:
    """The version tags move only after the release exists, on this run's digests."""
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    promote = publish["jobs"]["docker-promote"]
    assert_that(promote["needs"]).contains(
        "classify-tag",
        "docker-build",
        "release-gate",
        "github-release",
    )
    assert_that(_job_ancestors(publish, job_id="docker-promote")).contains(
        "release-gate",
        "pypi-upload",
        "github-release",
        "docker-build",
    )
    assert_that(_normalize_github_expr(str(promote["if"]))).contains(
        "needs.classify-tag.outputs.is_prerelease == 'false'",
    )
    assert_that(promote).does_not_contain_key("uses")
    # attestations: read, not write. Nothing new is attested; the attestation
    # is digest-bound and the job only verifies it, and with an explicit
    # permissions block an omitted scope is `none`, which makes the
    # attestations API call unauthorized (Codex on #2658).
    assert_that(promote["permissions"]).is_equal_to(
        {
            "contents": "read",
            "attestations": "read",
            "packages": "write",
            "id-token": "write",
        },
    )


def test_docker_promote_verifies_signs_then_retags_last() -> None:
    """Gh attestation verify -> cosign -> promote (x3) last, none bypassable.

    Verification runs on the staging digests before any retag: attestations
    are digest-bound, so it proves the same thing as verifying the promoted
    refs, and a missing attestation fails the job while the version tags
    are still unmoved (Codex on #2658).
    """
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    steps = _job_steps(publish, job="docker-promote")
    index_by_run = {str(step.get("run", "")): i for i, step in enumerate(steps)}
    index_by_id = {str(step["id"]): i for i, step in enumerate(steps) if "id" in step}
    promotes = [
        step
        for step in steps
        if step.get("run") == "scripts/ci/promote-ci-docker-images.sh"
    ]
    assert_that(promotes).is_length(3)
    assert_that({step["id"] for step in promotes}).is_equal_to(
        {"promote-base", "promote-full", "promote-ai"},
    )
    for step in promotes:
        env = step["env"]
        assert_that(env["CI_TAG"]).is_equal_to("build-${{ github.run_id }}")
        assert_that(env["EXPECTED_DIGEST"]).is_equal_to(
            _PROMOTED_IMAGE_DIGESTS[env["SOURCE_IMAGE"]],
        )
    assert_that({step["env"]["SOURCE_IMAGE"] for step in promotes}).is_equal_to(
        set(_PROMOTED_IMAGE_DIGESTS),
    )
    sign = index_by_run["scripts/ci/cosign-sign-images.sh"]
    verify = index_by_run["scripts/ci/verify-image-attestations.sh"]
    # Indexed by step id: the three promote steps share one ``run`` string,
    # so a run-keyed index collapses them onto the last one (Codex on #2658).
    promote_indexes = [index_by_id[step["id"]] for step in promotes]
    # Signing precedes every retag (Codex on #2659): a signature is bound to
    # the digest, so the staging digest's signature is the promoted tags'
    # signature, and a failure leaves the version tags unmoved. The retags
    # are the job's irreversible writes, so they are its last steps.
    assert_that(verify).is_less_than(sign)
    assert_that(sign).is_less_than(min(promote_indexes))
    assert_that(sorted(promote_indexes)).is_equal_to(
        list(range(len(steps) - 3, len(steps))),
    )
    assert_that(steps[-1]["run"]).is_equal_to("scripts/ci/promote-ci-docker-images.sh")
    verify_step = steps[verify]
    assert_that(verify_step["env"]["ATTESTATION_REPO"]).is_equal_to("lgtm-hq/py-lintro")
    assert_that(verify_step["env"]["SIGNER_REPO"]).is_equal_to("lgtm-hq/lgtm-ci")
    assert_that(verify_step["env"]).contains_key("GH_TOKEN")
    for image, digest in _PROMOTED_IMAGE_DIGESTS.items():
        # Verification and signing both target the staging digests the build
        # stage exported; the retags pin to the same digests.
        assert_that(str(verify_step["env"]["IMAGES"])).contains(f"{image}@{digest}")
        assert_that(str(steps[sign]["env"]["IMAGES"])).contains(f"{image}@{digest}")
    for step in (*promotes, steps[sign], verify_step):
        assert_that(step.get("continue-on-error")).described_as(step["name"]).is_none()
        assert_that(step.get("if")).described_as(step["name"]).is_none()
    for script in (
        "scripts/ci/promote-ci-docker-images.sh",
        "scripts/ci/cosign-sign-images.sh",
        "scripts/ci/verify-image-attestations.sh",
    ):
        assert_that(os.access(_REPO_ROOT / script, os.X_OK)).described_as(
            script,
        ).is_true()


def test_docker_staging_build_passes_no_version_or_latest_tag() -> None:
    """Staging pushes run-scoped tags only; version and latest wait for promote.

    The negated ``!inputs.staging && (...) || ''`` form is load-bearing: the
    naive ``inputs.staging && '' || <expr>`` falls through to ``<expr>``
    because the empty string is falsy in GitHub expressions. The ``release:``
    trigger (a rebuild-on-tag path) is gone; the backfill dispatch stays.
    """
    publish = _load_workflow(name="docker-build-publish.yml")
    triggers = publish["on"]
    assert_that(triggers).does_not_contain_key("release")
    call = triggers["workflow_call"]
    assert_that(call["inputs"]["staging"]["type"]).is_equal_to("boolean")
    assert_that(call["inputs"]["staging"]["default"]).is_false()
    assert_that({k: v["value"] for k, v in call["outputs"].items()}).is_equal_to(
        _RELEASE_IMAGE_DIGEST_OUTPUTS,
    )
    for job_id in _RELEASE_IMAGE_JOBS:
        with_block = publish["jobs"][job_id]["with"]
        version = _normalize_github_expr(str(with_block["version"]))
        assert_that(version).described_as(job_id).starts_with(
            "${{ !inputs.staging && (",
        )
        assert_that(version).described_as(job_id).ends_with(") || '' }}")
        assert_that(version).does_not_contain("github.event.release")
        assert_that(_normalize_github_expr(str(with_block["tag-latest"]))).described_as(
            job_id,
        ).starts_with("${{ !inputs.staging &&")
        assert_that(with_block["tags"]).described_as(job_id).is_equal_to(
            "${{ inputs.staging && format('build-{0}', github.run_id) || '' }}",
        )
        assert_that(with_block).does_not_contain_key("exact-tags")
        assert_that(_normalize_github_expr(str(with_block["push"]))).does_not_contain(
            "github.event_name == 'release'",
        )
    dispatch = triggers["workflow_dispatch"]["inputs"]
    assert_that(dispatch).contains_key(
        "backfill_version",
        "backfill_ref",
        "force_publish",
    )


def test_docker_promote_promotes_the_full_release_tag_set() -> None:
    """Each image gets <version>, <major.minor>, <major> and latest (#2658 nit).

    Since #2633 every floating tag is enabled by ``env.STABLE_TAGS`` and the
    candidate's version tag by ``env.RC_TAGS``; both derive from
    ``validation_channels`` (see the #2633 section), so with the switch off
    the set is exactly the four tags a stable release always had.
    """
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    steps = _job_steps(publish, job="docker-promote")
    metas = {
        str(step["id"]): step["with"]
        for step in steps
        if "docker/metadata-action@" in str(step.get("uses", ""))
    }
    expected_images = {
        "meta-base": "ghcr.io/lgtm-hq/py-lintro-base",
        "meta-full": "ghcr.io/lgtm-hq/py-lintro",
        "meta-ai": "ghcr.io/lgtm-hq/py-lintro-ai",
    }
    assert_that(set(metas)).is_equal_to(set(expected_images))
    stable = ",enable=${{ env.STABLE_TAGS }}"
    patterns = [
        "type=semver,pattern={{version}},value=${{ github.ref_name }}" + stable,
        "type=semver,pattern={{major}}.{{minor}},value=${{ github.ref_name }}" + stable,
        "type=semver,pattern={{major}},value=${{ github.ref_name }}" + stable,
        "type=pep440,pattern={{version}},value=${{ github.ref_name }}"
        ",enable=${{ env.RC_TAGS }}",
    ]
    for step_id, with_block in metas.items():
        assert_that(with_block["images"]).described_as(step_id).is_equal_to(
            expected_images[step_id],
        )
        assert_that(str(with_block["flavor"]).strip()).described_as(
            step_id,
        ).is_equal_to(
            "latest=${{ env.STABLE_TAGS }}",
        )
        tags = [line.strip() for line in str(with_block["tags"]).splitlines() if line]
        assert_that(tags).described_as(step_id).is_equal_to(
            patterns,
        )


_FREE_DISK_SCRIPT = "scripts/ci/free-disk-space.sh"
_BUILD_PUSH_ACTION = "docker/build-push-action@"
# The only gate the cleanup may carry: the docs-only skip shared by every
# heavy step in the job. Anything narrower (a fork check) leaves some
# hosted builds on the stock runner disk (#3085).
_FREE_DISK_ALLOWED_IFS = {None, "needs.changes.outputs.pipeline != 'false'"}


def _workflows_with_local_image_builds() -> dict[str, dict[str, Any]]:
    """Return every workflow that builds an image inline with build-push-action.

    Returns:
        Mapping of workflow file name to its parsed document.
    """
    workflows_dir = _REPO_ROOT / ".github" / "workflows"
    return {
        path.name: _load_workflow(name=path.name)
        for path in sorted(workflows_dir.glob("*.yml"))
        if _BUILD_PUSH_ACTION in path.read_text(encoding="utf-8")
    }


def _is_free_disk_step(step: dict[str, Any]) -> bool:
    """Return whether *step* runs the disk cleanup as its whole command.

    An exact match, so an ``echo`` or comment naming the script cannot pass
    for the cleanup itself.

    Args:
        step: Parsed workflow step.

    Returns:
        bool: True when the step's ``run`` is exactly the cleanup script.
    """
    return str(step.get("run", "")).strip() == _FREE_DISK_SCRIPT


@pytest.mark.parametrize(
    ("run", "expected"),
    [
        (_FREE_DISK_SCRIPT, True),
        (f"{_FREE_DISK_SCRIPT}\n", True),
        (f"echo 'skipping {_FREE_DISK_SCRIPT}'", False),
        (f"# {_FREE_DISK_SCRIPT}\ntrue", False),
        (None, False),
    ],
    ids=["exact", "trailing_newline", "echo", "comment", "no_run"],
)
def test_free_disk_step_match_requires_the_script_as_the_command(
    run: str | None,
    expected: bool,
) -> None:
    """Only a step whose command is the script counts as the cleanup.

    Args:
        run: The step's ``run`` value, or None for a ``uses`` step.
        expected: Whether the step should count as the cleanup.
    """
    step: dict[str, Any] = {} if run is None else {"run": run}
    assert_that(_is_free_disk_step(step)).is_equal_to(expected)


def test_docker_ci_is_scanned_for_hosted_image_builds() -> None:
    """The disk-cleanup contract below must actually cover docker-ci (#3085)."""
    assert_that(_workflows_with_local_image_builds()).contains_key("docker-ci.yml")


def test_hosted_image_builds_free_runner_disk_first() -> None:
    """Every hosted build-push job frees runner disk before its first build.

    docker-ci once ran ``free-disk-space.sh`` only for fork PRs, so main and
    same-repo PR builds started on the stock hosted-runner disk and the
    full/ai images ran buildkit out of space (#3085). The cleanup must run
    before the first ``docker/build-push-action`` step of each job, and its
    ``if:`` may not restrict it to a subset of the builds (e.g. forks).
    """
    offenders: list[str] = []
    for name, workflow in _workflows_with_local_image_builds().items():
        for job_id, job in workflow["jobs"].items():
            steps = job.get("steps") or []
            build_indexes = [
                index
                for index, step in enumerate(steps)
                if _BUILD_PUSH_ACTION in str(step.get("uses", ""))
            ]
            if not build_indexes:
                continue
            cleanups = [
                step for step in steps[: build_indexes[0]] if _is_free_disk_step(step)
            ]
            if not cleanups:
                offenders.append(f"{name}:{job_id}: no cleanup before first build")
                continue
            for step in cleanups:
                raw_if = step.get("if")
                normalized = (
                    None if raw_if is None else _normalize_github_expr(str(raw_if))
                )
                if normalized not in _FREE_DISK_ALLOWED_IFS:
                    offenders.append(
                        f"{name}:{job_id}: cleanup gated by {normalized!r}",
                    )
    assert_that(offenders).is_empty()
