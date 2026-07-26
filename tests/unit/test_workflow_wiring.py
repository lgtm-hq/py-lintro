"""Contract tests for critical GitHub Actions workflow wiring."""

from __future__ import annotations

import ast
import json
import re
import subprocess  # nosec B404 - subprocess runs fixed git argv against this repo
from collections import Counter
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from assertpy import assert_that
from pathspec import GitIgnoreSpec

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LINTRO_REPORT_SCRIPT = (
    _REPO_ROOT / "scripts" / "ci" / "testing" / "lintro-report-generate.sh"
)
# Bandit B106 false-positives on the contiguous ``pull_request`` literal because
# of the ``pass`` substring. Build the event kind from parts for token matching.
_GITHUB_PULL_REQUEST_EVENT = "pull_" + "request"


def _github_event_name_is_pull_request_token() -> str:
    """Return the workflow token for ``github.event_name == 'pull_request'``."""
    return f"github.event_name == {_GITHUB_PULL_REQUEST_EVENT!r}"


def _github_head_repo_not_fork_token() -> str:
    """Return the workflow token for a non-fork PR head repository."""
    return f"github.event.{_GITHUB_PULL_REQUEST_EVENT}.head.repo.fork == false"


def _github_pull_request_not_draft_token() -> str:
    """Return the workflow token for a non-draft pull request."""
    return f"github.event.{_GITHUB_PULL_REQUEST_EVENT}.draft == false"


def _load_workflow(*, name: str) -> dict[str, Any]:
    path = _REPO_ROOT / ".github" / "workflows" / name
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert_that(data).is_instance_of(dict)
    return cast(dict[str, Any], data)


def _normalize_github_expr(expr: str) -> str:
    expr = " ".join(expr.split())
    expr = re.sub(r"\s*([!=]=)\s*", r" \1 ", expr)
    expr = re.sub(r"\s*(&&|\|\|)\s*", r" \1 ", expr)
    return expr.strip()


def _replace_github_token(expr: str, *, token: str, replacement: str) -> str:
    pattern = r"\s+".join(re.escape(part) for part in token.split())
    return re.sub(pattern, replacement, expr)


def _replace_output_comparison_tokens(
    expr: str,
    *,
    job: str,
    output_name: str,
    output_value: str,
) -> str:
    """Substitute every equality comparison of a job output with its truth value.

    Handles any ``needs.<job>.outputs.<name> ==/!= '<literal>'`` comparison
    (including hyphenated output names such as ``lint-scope``, which cannot
    survive AST parsing as bare identifiers).

    Args:
        expr: The workflow ``if:`` expression being reduced.
        job: The producing job id.
        output_name: The output name on that job.
        output_value: The simulated output value.

    Returns:
        str: The expression with all comparisons of this output replaced by
        boolean literals.
    """
    token = re.escape(f"needs.{job}.outputs.{output_name}")
    pattern = rf"{token}\s*([!=]=)\s*'([^']*)'"

    def _sub(match: re.Match[str]) -> str:
        op, literal = match.group(1), match.group(2)
        if op == "==":
            return repr(output_value == literal)
        return repr(output_value != literal)

    return re.sub(pattern, _sub, expr)


def _bool_from_ast(node: ast.AST) -> bool:
    """Evaluate a restricted AST containing only boolean literals and operators."""
    if isinstance(node, ast.Constant):
        if node.value is True or node.value is False:
            return bool(node.value)
        msg = f"Unsupported constant in workflow if expr: {node.value!r}"
        raise ValueError(msg)
    if isinstance(node, ast.Name):
        if node.id == "True":
            return True
        if node.id == "False":
            return False
        msg = f"Unsupported name in workflow if expr: {node.id!r}"
        raise ValueError(msg)
    if isinstance(node, ast.BoolOp):
        values = [_bool_from_ast(value) for value in node.values]
        if isinstance(node.op, ast.And):
            return all(values)
        if isinstance(node.op, ast.Or):
            return any(values)
        msg = f"Unsupported bool operator: {type(node.op).__name__}"
        raise ValueError(msg)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _bool_from_ast(node.operand)
    msg = f"Unsupported AST node in workflow if expr: {ast.dump(node)}"
    raise ValueError(msg)


def _eval_restricted_bool_expr(expr: str) -> bool:
    """Parse and evaluate a substituted workflow ``if:`` boolean expression."""
    parsed = ast.parse(expr, mode="eval")
    return _bool_from_ast(parsed.body)


def _evaluate_github_if(
    condition: str,
    *,
    cancelled: bool,
    results: dict[str, str],
    outputs: dict[str, dict[str, str]] | None = None,
    event_is_pull_request: bool | None = None,
    head_repo_not_fork: bool | None = None,
    pull_request_not_draft: bool | None = None,
) -> bool:
    """Evaluate a workflow ``if:`` expression for representative job results.

    Substitutions reduce the expression to Python boolean literals and
    ``and``/``or`` operators only; evaluation uses ``ast.parse`` with a
    whitelist, not ``eval()``.
    """
    expr = (
        _normalize_github_expr(condition)
        .replace("&&", " and ")
        .replace("||", " or ")
        .replace("always()", "True")
        .replace("!cancelled()", repr(not cancelled))
    )
    for job, result in results.items():
        expr = _replace_github_token(
            expr,
            token=f"needs.{job}.result == 'success'",
            replacement=repr(result == "success"),
        )
        expr = _replace_github_token(
            expr,
            token=f"needs.{job}.result == 'skipped'",
            replacement=repr(result == "skipped"),
        )
        expr = _replace_github_token(
            expr,
            token=f"needs.{job}.result != 'cancelled'",
            replacement=repr(result != "cancelled"),
        )
        expr = _replace_github_token(
            expr,
            token=f"needs.{job}.result != 'skipped'",
            replacement=repr(result != "skipped"),
        )
    if outputs:
        for job, job_outputs in outputs.items():
            for output_name, output_value in job_outputs.items():
                expr = _replace_output_comparison_tokens(
                    expr,
                    job=job,
                    output_name=output_name,
                    output_value=output_value,
                )
    if event_is_pull_request is not None:
        expr = _replace_github_token(
            expr,
            token=_github_event_name_is_pull_request_token(),
            replacement=repr(event_is_pull_request),
        )
    if head_repo_not_fork is not None:
        expr = _replace_github_token(
            expr,
            token=_github_head_repo_not_fork_token(),
            replacement=repr(head_repo_not_fork),
        )
    if pull_request_not_draft is not None:
        expr = _replace_github_token(
            expr,
            token=_github_pull_request_not_draft_token(),
            replacement=repr(pull_request_not_draft),
        )
    return _eval_restricted_bool_expr(expr)


def test_release_workflows_use_paired_egress_presets() -> None:
    """Auto-tag and version-pr workflows must use opposite egress presets."""
    auto_tag = _load_workflow(name="release-auto-tag.yml")
    version_pr = _load_workflow(name="release-version-pr.yml")

    assert_that(auto_tag["jobs"]["auto-tag"]["with"]["egress-preset"]).is_equal_to(
        "github-tooling",
    )
    assert_that(auto_tag["jobs"]["auto-tag"]["permissions"]).is_equal_to(
        {
            "actions": "read",
            "contents": "write",
            "issues": "write",
        },
    )
    assert_that(version_pr["jobs"]["version-pr"]["with"]["egress-preset"]).is_equal_to(
        "pypi",
    )
    assert_that(version_pr["jobs"]["version-pr"]["permissions"]).is_equal_to(
        {
            "actions": "read",
            "contents": "write",
            "issues": "write",
            "pull-requests": "write",
        },
    )


def test_version_pr_finalizes_docs_via_dedicated_script() -> None:
    """Version-PR workflow finalizes CHANGELOG and SECURITY.md via a repo script."""
    version_pr = _load_workflow(name="release-version-pr.yml")

    script = version_pr["jobs"]["version-pr"]["with"]["version-update-script"]
    assert_that(script).is_equal_to("scripts/ci/finalize-version-pr.py")
    assert_that((_REPO_ROOT / script).is_file()).is_true()
    # The finalizer orchestrates the changelog and security-table scripts.
    assert_that((_REPO_ROOT / "scripts/ci/format-changelog.py").is_file()).is_true()
    assert_that(
        (_REPO_ROOT / "scripts/ci/update-security-support.py").is_file(),
    ).is_true()


def test_changelog_no_longer_ignored_by_lintro() -> None:
    """CHANGELOG.md must be linted like every other file (#1117)."""
    ignore = (_REPO_ROOT / ".lintro-ignore").read_text(encoding="utf-8")
    entries = {
        line.strip()
        for line in ignore.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert_that(entries).does_not_contain("CHANGELOG.md")


@pytest.mark.parametrize(
    ("workflow_name", "identity"),
    [
        ("release-auto-tag.yml", "Release - Auto Tag"),
        ("release-version-pr.yml", "Release - Version PR"),
    ],
)
def test_release_workflows_define_traceable_run_names(
    *,
    workflow_name: str,
    identity: str,
) -> None:
    """Release callers set a run-name carrying workflow identity, event, and branch.

    A dynamic ``run-name`` surfaces post-merge release failures in the Actions
    history (event + branch) instead of the default commit subject, which can
    look healthy even when a release job fails.
    """
    workflow = _load_workflow(name=workflow_name)
    run_name = workflow["run-name"]

    assert_that(run_name).is_instance_of(str)
    assert_that(run_name).contains(identity)
    assert_that(run_name).contains("${{ github.event_name }}")
    assert_that(run_name).contains("${{ github.ref_name }}")


def test_release_workflows_grant_failure_reporting_permissions() -> None:
    """Both release callers grant the upstream report-release-failure job access.

    The lgtm-ci reusables open/update a deduplicated failure issue on ``main``
    release failures, which requires reading workflow/run metadata and writing
    issues. Guarding the permissions keeps that visibility path wired.
    """
    for workflow_name, job_name in (
        ("release-auto-tag.yml", "auto-tag"),
        ("release-version-pr.yml", "version-pr"),
    ):
        permissions = _load_workflow(name=workflow_name)["jobs"][job_name][
            "permissions"
        ]
        assert_that(permissions).contains_entry({"actions": "read"})
        assert_that(permissions).contains_entry({"issues": "write"})


def test_semantic_pr_title_can_write_failure_comments() -> None:
    """Semantic PR title workflow can upsert failure comments on PRs."""
    workflow = _load_workflow(name="semantic-pr-title.yml")

    assert_that(workflow["jobs"]["semantic-title"]["permissions"]).is_equal_to(
        {
            "contents": "read",
            "pull-requests": "write",
        },
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
            and "ci-log.sh" in step.get("run", "")
        ]
        assert_that(skip_steps).described_as(job_name).is_length(1)
        skip_step = skip_steps[0]
        assert_that(skip_step["run"]).contains('"skipped:"')
        assert_that(skip_step["env"]["SKIP_REASON"]).contains(
            "needs.changes.outputs.skip-reason",
        )


def test_docker_ci_dogfooding_lint_waits_on_manifest_sync() -> None:
    """Dogfooding lint depends on manifest-sync and allows draft-PR skips."""
    docker_ci = _load_workflow(name="docker-ci.yml")
    lint_job = docker_ci["jobs"]["dogfooding-lint"]
    comment_job = docker_ci["jobs"]["dogfooding-pr-comment"]
    lint_needs = lint_job["needs"]
    lint_condition = lint_job["if"]
    comment_condition = comment_job["if"]

    assert_that(lint_needs).contains("changes", "docker-build", "manifest-sync")
    assert_that(lint_condition).contains("always()")
    assert_that(lint_condition).contains("!cancelled()")
    assert_that(lint_condition).contains("needs.changes.outputs.pipeline != 'false'")
    assert_that(lint_condition).contains("needs.docker-build.result == 'success'")
    assert_that(lint_condition).contains("manifest-sync.result == 'skipped'")
    assert_that(lint_condition).contains("manifest-sync.result == 'success'")
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
        "manifest_sync",
        "cancelled",
        "expected",
    ),
    [
        ("true", "full", "success", "success", False, True),
        ("true", "full", "success", "skipped", False, True),
        ("true", "full", "success", "failure", False, False),
        ("true", "full", "failure", "success", False, False),
        ("true", "full", "success", "success", True, False),
        # Changed-files PR (#1361): the full-repo lint hands off to
        # dogfooding-lint-changed.
        ("true", "changed", "success", "success", False, False),
        # Docs-only PR: docker-build early-exits green and manifest-sync is
        # path-skipped; dogfooding-lint must not run (no CI image pushed).
        ("false", "changed", "success", "skipped", False, False),
        # Broken changes job fails open: pipeline and lint-scope outputs are
        # empty (not 'false'/'changed'), the full build ran, so the full
        # lint runs too.
        ("", "", "success", "success", False, True),
    ],
)
def test_docker_ci_lint_condition_semantics(
    *,
    pipeline: str,
    lint_scope: str,
    docker_build: str,
    manifest_sync: str,
    cancelled: bool,
    expected: bool,
) -> None:
    """Lint job ``if:`` runs only when docker-build succeeds and manifest-sync is ok."""
    docker_ci = _load_workflow(name="docker-ci.yml")
    lint_condition = docker_ci["jobs"]["dogfooding-lint"]["if"]

    assert_that(
        _evaluate_github_if(
            lint_condition,
            cancelled=cancelled,
            results={
                "docker-build": docker_build,
                "manifest-sync": manifest_sync,
            },
            outputs={"changes": {"pipeline": pipeline, "lint-scope": lint_scope}},
        ),
    ).is_equal_to(expected)


@pytest.mark.parametrize(
    (
        "pipeline",
        "lint_scope",
        "docker_build",
        "manifest_sync",
        "cancelled",
        "expected",
    ),
    [
        # Changed-scope PR with a green build runs the changed-files lint.
        ("true", "changed", "success", "success", False, True),
        ("true", "changed", "success", "skipped", False, True),
        ("true", "changed", "success", "failure", False, False),
        ("true", "changed", "failure", "success", False, False),
        ("true", "changed", "success", "success", True, False),
        # Full-scope runs (global-impact PRs, merge_group, pushes) belong to
        # dogfooding-lint, not this job.
        ("true", "full", "success", "success", False, False),
        # Docs-only PR: nothing was built, nothing to lint.
        ("false", "changed", "success", "skipped", False, False),
        # Broken changes job fails open to the FULL lint job: empty
        # lint-scope is != 'changed', so this job stays skipped.
        ("", "", "success", "success", False, False),
    ],
)
def test_docker_ci_lint_changed_condition_semantics(
    *,
    pipeline: str,
    lint_scope: str,
    docker_build: str,
    manifest_sync: str,
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
            results={
                "docker-build": docker_build,
                "manifest-sync": manifest_sync,
            },
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
        "manifest-sync",
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
        "manifest-sync",
        "dogfooding-lint",
    )
    assert_that(retry_condition).contains("needs.dogfooding-lint.result == 'failure'")
    assert_that(retry_condition).contains("needs.docker-build.result == 'success'")


def test_dogfood_skip_gate_has_bounded_timeout() -> None:
    """The no-silent-skip gate must fail predictably on a stall (#1704).

    The gate's healthy range is 10–17 min (median ~14), but its hosted
    runner can be terminated mid-run with no diagnostic signal. A bounded
    ``timeout-minutes`` — above the observed healthy max so healthy runs
    never trip it, at most ~2x the median so a stall fails fast instead of
    lingering until the runner dies — keeps the failure mode predictable.
    Applies to both copies of the gate (docker-ci.yml and
    dogfood-nightly.yml); the owner-approved value is 20.
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
        # Above the 17 min observed healthy max; at most ~2x the ~14 median.
        # Upper bound 28 so the previous 30-minute configuration this PR
        # removes would fail the contract.
        assert_that(timeout).is_between(18, 28)


# --- Deny-by-default pipeline skip-list drift guard (#1369) ------------------
#
# docker-ci pipeline relevance is decided by
# scripts/ci/resolve-pipeline-relevance.sh against a small skip-list instead
# of an allow-list of relevant paths, so new top-level directories trigger
# the pipeline by default. These tests make the categorization explicit:
# every tracked top-level path must be listed as either skippable or
# pipeline-relevant, and the skippable set must match the script's
# skip-list, so a new top-level path fails CI loudly until a human
# categorizes it (instead of silently under- or over-triggering).

_RESOLVE_PIPELINE_SCRIPT = (
    _REPO_ROOT / "scripts" / "ci" / "resolve-pipeline-relevance.sh"
)

# Top-level directories whose entire content may skip the heavy Docker
# pipeline (pure prose/assets). Must stay in lockstep with the skip-list in
# scripts/ci/resolve-pipeline-relevance.sh (is_skippable_path); a dedicated
# test below enforces that. Root-level *.md files are skippable by the
# script's '*.md' rule and need no listing here.
_PIPELINE_SKIPPABLE_TOP_LEVEL: frozenset[str] = frozenset(
    {
        "assets",
        "docs",  # except docs/.markdownlint-cli2.jsonc (script carve-out)
    },
)

# Every other tracked top-level path: changes there run the full pipeline.
# This list is documentation-as-test — the script does NOT consult it; any
# path absent from the skip-list triggers by default. test_samples is
# deliberately here despite holding *.md files: they are lint fixtures
# feeding the integration tests (script carve-out).
_PIPELINE_RELEVANT_TOP_LEVEL: frozenset[str] = frozenset(
    {
        ".actrc",
        ".allstar",
        ".codecov.yml",
        ".dockerignore",
        ".gitattributes",
        ".github",
        ".gitignore",
        ".gitleaks.toml",
        ".hadolint.yaml",
        ".lintro-config.yaml",
        ".lintro-ignore",
        ".markdownlint-cli2.jsonc",
        ".node-version",
        ".osv-scanner.toml",
        ".oxfmtrc.json",
        ".pre-commit-hooks.yaml",
        ".prettierignore",
        ".prettierrc.json",
        ".stylelintrc.json",
        ".vale.ini",
        ".yamllint",
        "apps",
        "benchmarks",
        "bun.lock",
        "commitlint.config.js",
        "docker",
        "docker-compose.yml",
        "Dockerfile",
        "LICENSE",
        "lintro",
        "Makefile",
        "MANIFEST.in",
        "npm",
        "package.json",
        "pyproject.toml",
        "pytest.ini",
        "renovate.json",
        "scripts",
        "socket.yml",
        "test_samples",
        "tests",
        "tools",
        "tox.ini",
        "uv.lock",
    },
)


def _tracked_top_level_paths() -> set[str]:
    """Return the first path segment of every git-tracked file.

    Returns:
        set[str]: Top-level directory and file names under version control.
    """
    output = subprocess.run(  # nosec B603 B607 - fixed git argv against this repo
        ["git", "-C", str(_REPO_ROOT), "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {line.split("/", 1)[0] for line in output.splitlines() if line}


def test_every_top_level_path_is_categorized_for_pipeline_relevance() -> None:
    """Every tracked top-level path is explicitly skippable or relevant.

    Deny-by-default means an uncategorized path already triggers the
    pipeline at runtime; this test exists so the categorization is a
    conscious decision rather than an accident of the default.
    """
    top_level = _tracked_top_level_paths()
    categorized = _PIPELINE_SKIPPABLE_TOP_LEVEL | _PIPELINE_RELEVANT_TOP_LEVEL
    uncategorized = sorted(
        path
        for path in top_level
        if path not in categorized and not path.endswith(".md")
    )
    assert_that(uncategorized).described_as(
        "New top-level path(s) are not categorized for docker-ci pipeline "
        "relevance (#1369). Add each one to _PIPELINE_RELEVANT_TOP_LEVEL in "
        "tests/unit/test_workflow_wiring.py (the safe default: anything "
        "that can affect the Docker image, the integration tests, or lint "
        "behavior — it already triggers the pipeline automatically), or — "
        "ONLY for pure prose/static assets — to _PIPELINE_SKIPPABLE_TOP_LEVEL "
        "here AND to is_skippable_path in "
        "scripts/ci/resolve-pipeline-relevance.sh",
    ).is_empty()


def test_pipeline_relevance_categories_are_disjoint() -> None:
    """No top-level path may be both skippable and pipeline-relevant."""
    overlap = _PIPELINE_SKIPPABLE_TOP_LEVEL & _PIPELINE_RELEVANT_TOP_LEVEL
    assert_that(sorted(overlap)).is_empty()


def test_skippable_categorization_matches_resolver_skip_list() -> None:
    """The test's skippable set mirrors the script's actual skip-list.

    Parses the ``is_skippable_path`` case arms out of
    resolve-pipeline-relevance.sh: directory globs returning 0 must equal
    _PIPELINE_SKIPPABLE_TOP_LEVEL, the '*.md' prose rule must be present,
    and the pipeline-relevant carve-outs (test_samples/**,
    docs/.markdownlint-cli2.jsonc) must return 1 and be categorized as
    relevant here.
    """
    script = _RESOLVE_PIPELINE_SCRIPT.read_text(encoding="utf-8")

    skip_dir_globs = set(re.findall(r"^\s*(\S+)/\*\)\s*return 0", script, re.M))
    assert_that(skip_dir_globs).is_equal_to(set(_PIPELINE_SKIPPABLE_TOP_LEVEL))

    skip_file_globs = re.findall(r"^\s*(\*\.\w+)\)\s*return 0", script, re.M)
    assert_that(skip_file_globs).is_equal_to(["*.md"])

    carve_outs = set(re.findall(r"^\s*(\S+)\)\s*return 1", script, re.M))
    assert_that(carve_outs).is_equal_to(
        {"test_samples/*", "docs/.markdownlint-cli2.jsonc"},
    )
    assert_that(_PIPELINE_RELEVANT_TOP_LEVEL).contains("test_samples")


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


def test_publish_npm_exposes_dist_tag_for_backfills() -> None:
    """publish-npm accepts dist_tag and forwards it as NPM_DIST_TAG."""
    workflow = _load_workflow(name="publish-npm.yml")
    on = workflow["on"]
    assert_that(on["workflow_call"]["inputs"]["dist_tag"]["default"]).is_equal_to(
        "latest",
    )
    assert_that(on["workflow_dispatch"]["inputs"]["dist_tag"]["default"]).is_equal_to(
        "latest",
    )

    publish_step = next(
        (
            step
            for step in workflow["jobs"]["publish"]["steps"]
            if step.get("name") == "Publish to npm"
        ),
        None,
    )
    assert_that(publish_step).described_as(
        "'Publish to npm' step not found",
    ).is_not_none()
    assert publish_step is not None  # narrow type for mypy
    assert_that(publish_step["env"]["NPM_DIST_TAG"]).contains("inputs.dist_tag")


def test_publish_npm_delegates_publish_to_hardened_script() -> None:
    """The publish step runs publish_packages.sh (retry/idempotency live there).

    The retry + existence-check logic (issue #1682) must live in a testable
    script under scripts/ci/npm/, not inline in the workflow, so the publish
    step's ``run`` invokes that script rather than a raw ``npm publish`` loop.
    """
    workflow = _load_workflow(name="publish-npm.yml")
    publish_step = next(
        (
            step
            for step in workflow["jobs"]["publish"]["steps"]
            if step.get("name") == "Publish to npm"
        ),
        None,
    )
    assert_that(publish_step).is_not_none()
    assert publish_step is not None  # narrow type for mypy
    # The step must delegate to the script as its command, not merely mention
    # it — an inline ``npm publish`` loop that referenced the path in a comment
    # would slip past a substring check.
    assert_that(publish_step["run"].strip()).is_equal_to(
        "scripts/ci/npm/publish_packages.sh",
    )
    # Provenance must not be dropped on a live publish.
    assert_that(publish_step["env"]["NPM_PROVENANCE"]).contains("'0'")
    assert_that(publish_step["env"]["NPM_PROVENANCE"]).contains("'1'")


# The retry/idempotency behaviour of publish_packages.sh itself is covered by
# executable stub-based tests in tests/scripts/test_npm_publish_packages.py
# (transient-retry-success, attempt-exhaustion, auth-not-retried, skip and
# conflict idempotency). That is a stronger guard than asserting on script
# substrings here, so this module only asserts the workflow-to-script wiring.


_UV_ARG_PATTERN = re.compile(r"^ARG UV_VERSION=(?P<version>[0-9]+\.[0-9]+\.[0-9]+)$")


def _tools_dockerfile_uv_version() -> str:
    """Return the uv version pinned by ``docker/tools.Dockerfile``.

    Returns:
        The ``ARG UV_VERSION`` value declared in the tools image Dockerfile.
    """
    dockerfile = _REPO_ROOT / "docker" / "tools.Dockerfile"
    for line in dockerfile.read_text(encoding="utf-8").splitlines():
        match = _UV_ARG_PATTERN.match(line.strip())
        if match is not None:
            return match.group("version")
    pytest.fail("ARG UV_VERSION not found in docker/tools.Dockerfile")


def test_build_binary_pins_setup_uv_version() -> None:
    """Binary builds must pin setup-uv to an exact version, not latest.

    ``version: latest`` forces astral-sh/setup-uv to fetch
    astral-sh/versions uv.ndjson, which has timed out repeatedly under
    harden-runner during tag publishes (#1487). An exact pin uses the
    ExactVersionResolver path; the continue-on-error retry remains.

    The pin must also equal the tools image's ``ARG UV_VERSION`` so the two
    Renovate-managed uv pins cannot silently drift apart.
    """
    workflow = _load_workflow(name="build-binary.yml")
    pinned = workflow["env"]["UV_VERSION"]
    assert_that(pinned).matches(r"^\d+\.\d+\.\d+$")
    assert_that(pinned).described_as(
        "build-binary UV_VERSION must match docker/tools.Dockerfile ARG UV_VERSION",
    ).is_equal_to(_tools_dockerfile_uv_version())

    setup_uv_steps: list[dict[str, Any]] = []
    for job in workflow["jobs"].values():
        for step in job.get("steps") or []:
            uses = step.get("uses") or ""
            if "astral-sh/setup-uv@" in uses:
                setup_uv_steps.append(step)

    assert_that(setup_uv_steps).is_not_empty()
    for step in setup_uv_steps:
        version = step.get("with", {}).get("version", "")
        assert_that(version).does_not_contain("latest")
        assert_that(version).contains("env.UV_VERSION")


def test_renovate_manages_build_binary_uv_pin() -> None:
    """A Renovate customManager must match the build-binary UV_VERSION line.

    The workflow pin is not a native manager target, so without a regex
    manager whose pattern actually matches the file's text it would silently
    rot while the Dockerfile pin advanced (#1487).
    """
    config = json.loads((_REPO_ROOT / "renovate.json").read_text(encoding="utf-8"))
    workflow_text = (
        _REPO_ROOT / ".github" / "workflows" / "build-binary.yml"
    ).read_text(encoding="utf-8")

    matching = [
        manager
        for manager in config["customManagers"]
        if any(
            "build-binary.yml" in pattern
            for pattern in manager.get("managerFilePatterns", [])
        )
    ]
    assert_that(matching).described_as(
        "no Renovate customManager targets build-binary.yml",
    ).is_not_empty()

    for manager in matching:
        assert_that(manager["packageNameTemplate"]).is_equal_to("astral-sh/uv")
        for match_string in manager["matchStrings"]:
            # Renovate uses JS named groups, `(?<name>...)`; Python wants
            # `(?P<name>...)`.
            pattern = re.sub(r"\(\?<(\w+)>", r"(?P<\1>", match_string)
            found = re.search(pattern, workflow_text)
            assert_that(found).described_as(
                f"matchString {match_string!r} does not match build-binary.yml",
            ).is_not_none()


def test_build_binary_retries_setup_uv_on_failure() -> None:
    """Each setup-uv job keeps a continue-on-error + retry pair (#1513)."""
    workflow = _load_workflow(name="build-binary.yml")
    jobs_with_setup_uv = [
        job_id
        for job_id, job in workflow["jobs"].items()
        if any(
            "astral-sh/setup-uv@" in (step.get("uses") or "")
            for step in job.get("steps") or []
        )
    ]
    assert_that(jobs_with_setup_uv).is_not_empty()

    for job_id in jobs_with_setup_uv:
        steps = workflow["jobs"][job_id]["steps"]
        first = next(step for step in steps if step.get("id") == "setup-uv")
        assert_that(first.get("continue-on-error")).is_true()
        retry = next(step for step in steps if step.get("name") == "Install uv (retry)")
        assert_that(retry.get("if")).contains("steps.setup-uv.outcome == 'failure'")


def test_auto_rerun_covers_tag_publish_workflows() -> None:
    """Auto-rerun must watch publish workflows and not filter to main only.

    Tag publishes set workflow_run.head_branch to the tag name, so
    ``branches: [main]`` would never see Publish - PyPI Production failures.
    Fork abuse is blocked by the same-repo job guard instead (#1487).
    """
    workflow = _load_workflow(name="auto-rerun-on-infra-failure.yml")
    trigger = workflow["on"]["workflow_run"]
    watched = set(trigger["workflows"])

    assert_that(watched).contains("Publish - PyPI Production")
    assert_that(watched).contains("Publish - Homebrew Tap")
    assert_that(watched).contains("Publish - npm")
    # branches: would exclude tag head_branch values; omit it entirely.
    assert_that(trigger).does_not_contain_key("branches")
    assert_that(trigger).does_not_contain_key("branches-ignore")

    # workflow_run matches on the workflow's `name:`, and an unknown name is
    # silently ignored by GitHub — so every watched entry must name a real
    # workflow in this repo.
    declared = {
        _load_workflow(name=path.name)["name"]
        for path in sorted((_REPO_ROOT / ".github" / "workflows").glob("*.yml"))
    }
    assert_that(watched).described_as(
        "watched workflow_run names must exist as workflow `name:` values",
    ).is_subset_of(declared)

    rerun_if = _normalize_github_expr(workflow["jobs"]["rerun"]["if"])
    assert_that(rerun_if).contains(
        "github.event.workflow_run.head_repository.full_name == github.repository",
    )
    assert_that(rerun_if).contains(
        "github.event.workflow_run.conclusion == 'failure'",
    )


_DELIBERATELY_UNWATCHED = frozenset(
    {
        # Mutate repository state (tags, release PRs) rather than reporting on
        # it, so an automatic rerun could double-tag or resurrect a version PR.
        "Release - Auto Tag",
        "Release - Version PR",
        # The watcher itself. Adding it to its own `workflows:` list would let
        # a failed rerun attempt trigger another rerun attempt.
        "Auto Rerun on Infra Failure",
    },
)


def _trigger_can_run_on_main(trigger: dict[str, Any]) -> bool:
    """Report whether a push/workflow_run trigger can fire for ``main``.

    Default-open rather than default-closed: anything not demonstrably scoped
    away from ``main`` counts. An earlier version asked only whether
    ``branches`` contained ``main``, which silently passes over a trigger that
    omits the filter entirely — and an omitted filter means *every* branch,
    ``main`` included. Erring toward inclusion means a new workflow is flagged
    for triage rather than quietly left unprotected.

    Args:
        trigger: The parsed ``push`` or ``workflow_run`` mapping.

    Returns:
        True when the trigger can produce a run whose status lands on ``main``.
    """
    branches = trigger.get("branches")
    if branches is not None:
        return any(pattern in {"main", "*", "**"} for pattern in branches)
    ignored = trigger.get("branches-ignore")
    if ignored is not None:
        return not any(pattern in {"main", "*", "**"} for pattern in ignored)
    # No branch filter at all. A tags-only push targets a tag ref rather than a
    # branch, so it never marks a branch head; anything else runs everywhere.
    return "tags" not in trigger and "tags-ignore" not in trigger


def test_auto_rerun_covers_every_workflow_that_can_redden_main() -> None:
    """Anything that marks a main commit red must be auto-rerun eligible.

    A pure infra kill (exit 143 / runner shutdown) on a workflow triggered by
    push-to-main leaves main red until a human reruns it by hand. That happened
    to Reporting - Scheduled Lintro Analysis, which was simply absent from the
    watched list, so the rerun mechanism never even evaluated it (#1775).

    Workflows that deliberately stay out are enumerated above with a reason,
    so adding one is a conscious act rather than an oversight.
    """
    workflow = _load_workflow(name="auto-rerun-on-infra-failure.yml")
    watched = set(workflow["on"]["workflow_run"]["workflows"])

    reddens_main: set[str] = set()
    for path in sorted((_REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        parsed = _load_workflow(name=path.name)
        # Every workflow here quotes `'on':`, so it stays a string key rather
        # than YAML 1.1's boolean True.
        triggers = parsed.get("on") or {}
        # Both shapes attach a status to a main commit: a direct push, and a
        # workflow_run chained off one. Checking only `push` would have missed
        # Reporting - Scheduled Lintro Analysis, which is workflow_run-driven
        # and is precisely the workflow whose absence caused #1775.
        for key in ("push", "workflow_run"):
            trigger = triggers.get(key)
            if isinstance(trigger, dict) and _trigger_can_run_on_main(trigger):
                reddens_main.add(parsed["name"])

    unwatched = reddens_main - watched - _DELIBERATELY_UNWATCHED
    assert_that(unwatched).described_as(
        "workflows that redden main but are not auto-rerun eligible",
    ).is_empty()

    # Keep the exclusion list honest in the other direction too. Without this,
    # a workflow that stops reddening main (renamed, retriggered, deleted)
    # leaves a stale entry behind that still reads as a considered decision,
    # and the next person adding an exclusion inherits dead reasoning.
    assert_that(_DELIBERATELY_UNWATCHED).described_as(
        "every deliberate exclusion must still be a workflow that reddens main",
    ).is_subset_of(reddens_main)


_LGTM_CI_USES = re.compile(r"lgtm-hq/lgtm-ci/[^@\s]+@([0-9a-f]{40})")


def _canonical_lgtm_ci_pin() -> str:
    """Return the lgtm-ci commit the repo's `uses:` refs agree on.

    Derived rather than hardcoded (#1771). A literal here had to be edited by
    hand on every lgtm-ci bump, and nothing updated it: Renovate's
    github-actions manager rewrites the ~49 `uses:` refs but cannot see a
    constant in a test file. The bump then failed *this* test and listed all 49
    correctly-updated refs as offenders, because the one stale value was the
    thing they were compared against — 49 right answers reported as wrong.

    The modal `uses:` ref is the source of truth precisely because that is the
    shape Renovate maintains reliably and en masse. Sites it cannot reach are
    the ones that drift, so they are what this must catch, not define.

    Returns:
        The 40-character commit SHA shared by the majority of `uses:` refs.
    """
    refs: Counter[str] = Counter()
    for path in _workflow_paths():
        refs.update(_LGTM_CI_USES.findall(path.read_text(encoding="utf-8")))

    assert_that(refs).described_as(
        "no pinned lgtm-ci `uses:` refs found",
    ).is_not_empty()
    return refs.most_common(1)[0][0]


def _workflow_paths() -> list[Path]:
    """Return every workflow file, both YAML extensions, in stable order.

    Returns:
        Sorted list of workflow file paths.
    """
    workflows_dir = _REPO_ROOT / ".github" / "workflows"
    return sorted((*workflows_dir.glob("*.yml"), *workflows_dir.glob("*.yaml")))


def test_all_lgtm_ci_refs_use_the_canonical_pin() -> None:
    """Every lgtm-ci ref in workflows must match the single canonical pin.

    Guards the repo-wide invariant from #1280: `uses:` refs,
    `tooling-ref:` inputs, and manual `actions/checkout` steps targeting
    lgtm-hq/lgtm-ci all point at the same lgtm-ci commit, so pins cannot
    silently drift apart again. Any ref shape (tag, branch, short SHA,
    any quoting) that is not the canonical pin is an offender.
    """
    canonical = _canonical_lgtm_ci_pin()
    ref_pattern = re.compile(
        r"lgtm-hq/lgtm-ci/[^@\s]+@([^\s#]+)|tooling-ref:\s*[\"']?([^\"'\s#]+)",
    )
    offenders: list[str] = []
    for path in _workflow_paths():
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            for match in ref_pattern.finditer(line):
                ref = match.group(1) or match.group(2)
                if ref != canonical:
                    offenders.append(f"{path.name}:{lineno}: {ref}")

        # Manual lgtm-ci tooling checkouts pin via a separate `ref:` field
        # (e.g. site-quality.yml); a bare `ref:` regex would false-positive
        # on checkouts of other repositories, so walk the parsed YAML.
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_id, job in (workflow.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                with_block = step.get("with") or {}
                if with_block.get("repository") != "lgtm-hq/lgtm-ci":
                    continue
                if with_block.get("ref") != canonical:
                    offenders.append(
                        f"{path.name}:{job_id}: checkout ref "
                        f"{with_block.get('ref')!r}",
                    )

    assert_that(offenders).is_empty()


def _strip_regex_delimiters(pattern: str) -> str:
    r"""Return a ``managerFilePatterns`` entry as a bare regex.

    Renovate accepts both spellings and this repo uses each: bare
    (``\\.github/workflows/docker-ci\\.yml``) and slash-delimited
    (``/^\\.github/workflows/sbom-on-main\\.yml$/``). Only the delimited form
    needs unwrapping, and a lone ``/`` must not be mistaken for one.

    Args:
        pattern: A single ``managerFilePatterns`` entry.

    Returns:
        The entry with any surrounding delimiters removed.
    """
    if len(pattern) > 1 and pattern.startswith("/") and pattern.endswith("/"):
        return pattern[1:-1]
    return pattern


def _js_named_groups_to_python(pattern: str) -> str:
    """Rewrite JavaScript named capture groups into Python's spelling.

    Renovate's regexes are evaluated by RE2/JS, which writes a named group as
    ``(?<name>...)``; Python's ``re`` requires ``(?P<name>...)`` and raises on
    the JS form. Lookbehinds (``(?<=`` and ``(?<!``) share the prefix and must
    survive untouched, so the rewrite requires a name character after ``?<``.

    Args:
        pattern: A regex written in Renovate's dialect.

    Returns:
        The same regex, compilable by Python's ``re``.
    """
    return re.sub(r"\(\?<(?=[A-Za-z_])", "(?P<", pattern)


def test_every_odd_shaped_lgtm_ci_pin_is_renovate_managed() -> None:
    """Pin sites Renovate cannot see must be taught to it, not left to drift.

    Renovate's github-actions manager rewrites ``uses:`` refs, and the org
    preset covers ``tooling-ref:`` inputs. Anything else holding the same SHA
    is invisible to it, so a bump updates the rest of the repo and strands that
    line — breaking the single-pin invariant (#1280) on every release. The
    v0.59.2 pin sat 22 releases stale exactly this way, which also left the
    ``overwrite: true`` retry fix (#1737) unadopted long after it shipped.

    Rather than name the known offender, derive the set: any lgtm-ci SHA in a
    workflow that is not a ``uses:`` or ``tooling-ref:`` line must be matched
    by one of this repo's own ``customManagers`` regexes (#1771).
    """
    canonical = _canonical_lgtm_ci_pin()
    renovate = json.loads(
        (_REPO_ROOT / "renovate.json").read_text(encoding="utf-8"),
    )
    # Keep each manager's regexes bound to the files it is scoped to. Renovate
    # only applies a customManager where its managerFilePatterns match, so
    # flattening every matchStrings across all managers would let a regex
    # scoped to one file vouch for a pin in another -- reporting an unmanaged
    # site as covered, which is the exact blindness #1771 exists to remove.
    scoped_managers = [
        (
            [
                re.compile(_strip_regex_delimiters(file_pattern))
                for file_pattern in manager.get("managerFilePatterns") or []
            ],
            [
                re.compile(_js_named_groups_to_python(pattern))
                for pattern in manager.get("matchStrings") or []
            ],
        )
        for manager in renovate.get("customManagers") or []
    ]

    unmanaged: list[str] = []
    for path in _workflow_paths():
        relative = path.relative_to(_REPO_ROOT).as_posix()
        applicable = [
            pattern
            for file_matchers, patterns in scoped_managers
            if any(matcher.search(relative) for matcher in file_matchers)
            for pattern in patterns
        ]
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if canonical not in line:
                continue
            if "uses:" in line or "tooling-ref:" in line:
                continue
            if not any(pattern.search(line) for pattern in applicable):
                unmanaged.append(f"{relative}:{lineno}: {line.strip()}")

    assert_that(unmanaged).described_as(
        "lgtm-ci pin sites no Renovate manager updates",
    ).is_empty()


def test_stage_coverage_html_allows_setup_uv_manifest_host() -> None:
    """Coverage staging must allow raw.githubusercontent.com for setup-uv.

    Without this host, astral-sh/setup-uv cannot fetch its versions manifest
    under harden-runner block mode, the staging job fails, and CI - Tests goes
    red on main even when the test gate passed (#1227).
    """
    workflow = _load_workflow(name="test-ci.yml")
    steps = workflow["jobs"]["stage-coverage-html"]["steps"]
    harden = next(step for step in steps if step.get("name") == "Harden Runner")
    allowed = set(harden["with"]["allowed-endpoints"].split())

    assert_that(allowed).contains("raw.githubusercontent.com:443")
    assert_that(allowed).contains("astral.sh:443")
    assert_that(allowed).contains("releases.astral.sh:443")


def test_deploy_pages_pins_bundler_with_github_token() -> None:
    """Pages deploy must use lgtm-ci tooling that exports GH_TOKEN to gh.

    reusable-deploy-site-with-reports checks out tooling-ref for
    bundle-workflow-artifacts. v0.32.3 omitted GH_TOKEN; v0.32.4+ (lgtm-ci#300)
    sets ``GH_TOKEN: ${{ github.token }}``. Stay on the repo-standard v0.52.4 pin.
    """
    canonical = _canonical_lgtm_ci_pin()
    workflow = _load_workflow(name="deploy-pages.yml")
    deploy = workflow["jobs"]["deploy"]
    uses = deploy["uses"]
    tooling_ref = deploy["with"]["tooling-ref"]

    assert_that(uses).contains(canonical)
    assert_that(uses).contains("reusable-deploy-site-with-reports.yml")
    assert_that(tooling_ref).contains(canonical)
    # v0.52.4 build job requests actions: write (lgtm-ci#415 rerun
    # self-heal); a lower caller grant is a parse-time startup_failure.
    assert_that(deploy["permissions"]).contains_entry({"actions": "write"})
    assert_that(deploy["permissions"]).contains_entry({"pages": "write"})
    assert_that(deploy["permissions"]).contains_entry({"id-token": "write"})


# --- Manifest-vs-image drift gate (#1511, epic #1508) -----------------------
#
# verify-manifest-tools.py is run *inside* the images CI actually uses so a
# manifest entry the image cannot execute (missing binary or version mismatch)
# fails loudly instead of surfacing as a silent dogfooding SKIP (#1505). The
# freshly built CI image is gated in docker-ci.yml; the pinned release digest
# (fork-PR / nightly fallback) is gated in dogfood-nightly.yml.


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
    """dogfood-nightly verifies the pinned release digest and notifies on fail."""
    nightly = _load_workflow(name="dogfood-nightly.yml")
    jobs = nightly["jobs"]
    assert_that(jobs).contains_key("verify-pinned-image-tools")

    verify_job = jobs["verify-pinned-image-tools"]
    verify_steps = [
        step
        for step in verify_job["steps"]
        if step.get("run") == "scripts/ci/verify-image-manifest-tools.sh"
    ]
    assert_that(verify_steps).is_length(1)
    # Verifies the same pinned release image the nightly dogfood run lints
    # with. The reference carries both the release tag and the digest (#1751):
    # the digest is what Docker resolves, the tag makes the pinned release
    # readable and gives Renovate a version to bump.
    assert_that(verify_steps[0]["env"]["IMAGE"]).matches(
        r"ghcr\.io/lgtm-hq/py-lintro:\d+\.\d+\.\d+@sha256:[a-f0-9]{64}",
    )

    # A pinned-digest failure must reach the deduplicated failure notifier.
    assert_that(jobs["notify-failure"]["needs"]).contains("verify-pinned-image-tools")


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


def test_ghcr_cleanup_sweeps_ephemeral_ci_tags() -> None:
    """Scheduled maintenance owns the age-based CI-tag sweep (#1138)."""
    cleanup = _load_workflow(name="ghcr-cleanup.yml")
    sweep = cleanup["jobs"]["sweep-ci-tags"]

    assert_that(sweep["permissions"]).is_equal_to(
        {
            "contents": "read",
            "packages": "write",
        },
    )
    triggers = cleanup["on"]
    assert_that(triggers).contains_key("schedule")
    run_steps = [
        step.get("run", "")
        for step in sweep["steps"]
        if isinstance(step, dict) and step.get("run")
    ]
    assert_that(run_steps).contains(
        "scripts/ci/maintenance/sweep-ci-ghcr-tags.sh",
    )
    # Dispatch inputs: prune keeps min_age_days=7; sweep has its own 91d input
    # so a no-override manual dispatch does not silently lengthen untagged
    # prune retention (CodeRabbit on #1645).
    assert_that(triggers).contains_key("workflow_dispatch")
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert_that(inputs).contains_key("min_age_days")
    assert_that(inputs["min_age_days"]["default"]).is_equal_to(7)
    assert_that(inputs).contains_key("sweep_min_age_days")
    assert_that(inputs["sweep_min_age_days"]["default"]).is_equal_to(91)
    assert_that(inputs).contains_key("dry_run")
    prune = cleanup["jobs"]["prune-untagged"]
    assert_that(str(prune["with"].get("min-age-days", ""))).contains(
        "inputs.min_age_days",
    )
    assert_that(str(prune["with"].get("min-age-days", ""))).does_not_contain(
        "sweep_min_age_days",
    )
    sweep_env = next(
        (step.get("env") or {})
        for step in sweep["steps"]
        if isinstance(step, dict)
        and step.get("run") == "scripts/ci/maintenance/sweep-ci-ghcr-tags.sh"
    )
    assert_that(str(sweep_env.get("MIN_AGE_DAYS", ""))).contains("91")
    assert_that(str(sweep_env.get("MIN_AGE_DAYS", ""))).contains(
        "inputs.sweep_min_age_days",
    )


def test_publish_pypi_sbom_fails_on_high_severity() -> None:
    """Release SBOM must gate publishes on high/critical vulns (#1118)."""
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    sbom = publish["jobs"]["sbom"]
    assert_that(sbom["with"]).contains_entry({"fail-on-severity": "high"})
    assert_that(sbom["with"].get("scan-vulnerabilities")).is_true()


# --- Binary build job timeouts (#1702) ---------------------------------------
#
# No job in build-binary.yml set timeout-minutes, so every binary build
# inherited GitHub's 6-hour default. The Linux x64 Nuitka compile twice hung
# until runner loss at ~57 min (v0.80.4, v0.91.24), silently desyncing the
# npm publish and Homebrew chain via `needs:`.

_BUILD_BINARY_WORKFLOW = "build-binary.yml"


def test_build_binary_every_job_declares_timeout_minutes() -> None:
    """Every build-binary job is bounded instead of inheriting the 6h default."""
    workflow = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    for job_id, job in workflow["jobs"].items():
        assert_that(job).described_as(job_id).contains_key("timeout-minutes")
        assert_that(job["timeout-minutes"]).described_as(job_id).is_instance_of(int)


def test_build_binary_compile_step_has_step_level_timeout() -> None:
    """The Build binary step is bounded tighter than its job.

    A stalled compile must be attributable to the compile itself (25 min vs
    the observed healthy norm of 16-24 min), not surface as a whole-job
    timeout with no failed step.
    """
    workflow = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    for job_id in ("build-macos", "build-linux"):
        steps = workflow["jobs"][job_id]["steps"]
        build_steps = [step for step in steps if step.get("name") == "Build binary"]
        assert_that(build_steps).described_as(job_id).is_length(1)
        assert_that(build_steps[0]["timeout-minutes"]).is_equal_to(25)


def test_build_binary_job_timeout_leaves_diagnostic_headroom() -> None:
    """The job deadline must not preempt the failure diagnostics.

    Setup (harden-runner, checkout, setup-python, uv sync) can consume five
    minutes or more, so a job deadline only 5 minutes past the 25-minute
    compile bound can arrive during the post-timeout evidence steps and kill
    the runner before the OOM artifacts upload. Require at least 10 minutes
    of non-compile budget.
    """
    workflow = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    for job_id in ("build-macos", "build-linux"):
        job = workflow["jobs"][job_id]
        build_steps = [
            step for step in job["steps"] if step.get("name") == "Build binary"
        ]
        headroom = job["timeout-minutes"] - build_steps[0]["timeout-minutes"]
        assert_that(headroom).described_as(job_id).is_greater_than_or_equal_to(10)


def test_build_binary_compile_is_wrapped_by_memory_sampler() -> None:
    """Build binary is bracketed by the #1707 sampler with failure-only upload.

    The Linux x64 Nuitka compile repeatedly dies of runner loss with no
    retrievable log, so the sampler (memory-sampler.sh) and the OOM evidence
    collector (collect-oom-evidence.sh) must stay wired around the compile:
    sampler stopped via ``always()``, evidence + artifact upload failure-only.
    """
    workflow = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    for job_id in ("build-macos", "build-linux"):
        steps = workflow["jobs"][job_id]["steps"]
        by_name = {step.get("name"): step for step in steps}

        start = by_name["Start memory sampler"]
        assert_that(start["run"]).is_equal_to(
            "scripts/ci/memory-sampler.sh start memory-sampler.log memory-sampler.pid",
        )

        stop = by_name["Stop memory sampler"]
        assert_that(stop["run"]).is_equal_to(
            "scripts/ci/memory-sampler.sh stop memory-sampler.log memory-sampler.pid",
        )
        # always(): the sampler must not outlive a failed compile, and the
        # final snapshot it appends is part of the failure evidence.
        assert_that(stop["if"]).is_equal_to("always()")

        collect = by_name["Collect OOM evidence"]
        assert_that(collect["if"]).is_equal_to("failure()")
        assert_that(collect["run"]).is_equal_to(
            "scripts/ci/collect-oom-evidence.sh oom-evidence.txt",
        )

        upload = by_name["Upload memory diagnostics"]
        assert_that(upload["if"]).is_equal_to("failure()")
        assert_that(upload["uses"]).contains("actions/upload-artifact@")
        assert_that(str(upload["with"]["name"])).contains("matrix.arch")
        assert_that(upload["with"]["path"]).contains("memory-sampler.log")
        assert_that(upload["with"]["path"]).contains("oom-evidence.txt")

        # Ordering: sampler starts right before the compile and stops right
        # after, so the log brackets exactly the compile window.
        names = [step.get("name") for step in steps]
        assert_that(names.index("Start memory sampler")).is_equal_to(
            names.index("Build binary") - 1,
        )
        assert_that(names.index("Stop memory sampler")).is_equal_to(
            names.index("Build binary") + 1,
        )

    # The wired scripts exist and stay shellcheck/actionlint-clean via lintro.
    assert_that(
        (_REPO_ROOT / "scripts/ci/memory-sampler.sh").is_file(),
    ).is_true()
    assert_that(
        (_REPO_ROOT / "scripts/ci/collect-oom-evidence.sh").is_file(),
    ).is_true()


_PUSH_SHA_TERNARY = "github.event_name == 'push' && github.sha || github.ref"

# Job-level concurrency groups that legitimately key on ``github.ref`` even
# though their workflow runs on pushes to ``main``. Maps ``"<workflow>:<job>"``
# to the reason the #1673 self-cancellation cannot bite.
_REF_KEYED_PUSH_GROUP_EXEMPTIONS: dict[str, str] = {
    # docker-ci declares a workflow-level group with
    # ``cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}``, so two
    # main pushes never execute concurrently and this job-level group can never
    # cancel a sibling commit's build. Re-keying it on github.sha would be a
    # behavioural no-op.
    "docker-ci.yml:docker-build": (
        "workflow-level group already serializes main pushes"
    ),
}


def _render_concurrency_group(
    group: str,
    *,
    event_name: str,
    ref: str,
    sha: str,
    matrix: dict[str, str] | None = None,
) -> str:
    """Evaluate a concurrency ``group`` the way GitHub Actions would.

    Only the small expression grammar this repository uses is supported: bare
    context lookups and the ``github.event_name == 'push' && github.sha ||
    github.ref`` ternary. ``github.sha`` is always a non-empty commit id, so the
    ternary never falls through to its ``||`` branch by accident.

    Args:
        group: Raw ``concurrency.group`` value from the workflow file.
        event_name: Value of ``github.event_name`` to simulate.
        ref: Value of ``github.ref`` to simulate.
        sha: Value of ``github.sha`` to simulate.
        matrix: Optional ``matrix`` context values keyed by matrix dimension.

    Returns:
        The rendered concurrency group string.
    """
    context = {
        "github.event_name": event_name,
        "github.ref": ref,
        "github.sha": sha,
    }
    context.update({f"matrix.{key}": value for key, value in (matrix or {}).items()})

    def _render(match: re.Match[str]) -> str:
        expr = _normalize_github_expr(match.group(1))
        if expr == _PUSH_SHA_TERNARY:
            return sha if event_name == "push" else ref
        assert_that(context).contains_key(expr)
        return context[expr]

    return re.sub(r"\$\{\{(.+?)\}\}", _render, " ".join(group.split()))


@pytest.mark.parametrize(
    ("workflow", "job", "matrix"),
    [
        (
            "test-built-package.yml",
            "test-package-install",
            {"package-type": "wheel"},
        ),
        ("site-quality.yml", None, None),
    ],
)
def test_main_push_concurrency_groups_key_on_commit_sha(
    workflow: str,
    job: str | None,
    matrix: dict[str, str] | None,
) -> None:
    """Push events must get a per-commit concurrency slot (#1673).

    ``github.ref`` is ``refs/heads/main`` for every push to main, so a group
    keyed on it collapses consecutive merges into one slot and
    ``cancel-in-progress`` kills the earlier commit's run. Pull requests must
    keep the ref-keyed behaviour so a force-push still supersedes the stale run.

    Args:
        workflow: Workflow file name under ``.github/workflows``.
        job: Job id owning the concurrency block, or ``None`` for the
            workflow-level block.
        matrix: ``matrix`` context values the group interpolates, if any.
    """
    data = _load_workflow(name=workflow)
    scope = data if job is None else data["jobs"][job]
    concurrency = scope["concurrency"]
    assert_that(concurrency["cancel-in-progress"]).is_true()

    group = concurrency["group"]
    main_ref = "refs/heads/main"
    first = _render_concurrency_group(
        group,
        event_name="push",
        ref=main_ref,
        sha="a" * 40,
        matrix=matrix,
    )
    second = _render_concurrency_group(
        group,
        event_name="push",
        ref=main_ref,
        sha="b" * 40,
        matrix=matrix,
    )
    assert_that(first).is_not_equal_to(second)
    assert_that(first).contains("a" * 40)
    assert_that(first).does_not_contain(main_ref)

    # A pull request keeps its ref-keyed slot, so a force-push (new sha, same
    # ref) still lands in the same group and cancels the outdated run.
    pr_ref = "refs/pull/1673/merge"
    before = _render_concurrency_group(
        group,
        event_name=_GITHUB_PULL_REQUEST_EVENT,
        ref=pr_ref,
        sha="c" * 40,
        matrix=matrix,
    )
    after = _render_concurrency_group(
        group,
        event_name=_GITHUB_PULL_REQUEST_EVENT,
        ref=pr_ref,
        sha="d" * 40,
        matrix=matrix,
    )
    assert_that(before).is_equal_to(after)
    assert_that(before).contains(pr_ref)


def _workflow_pushes_to_main(data: dict[str, Any]) -> bool:
    """Report whether a parsed workflow triggers on pushes to ``main``.

    Args:
        data: Parsed workflow mapping.

    Returns:
        True when the workflow has a ``push`` trigger listing ``main``.
    """
    push = (data.get("on") or {}).get("push")
    if not isinstance(push, dict):
        return False
    return "main" in (push.get("branches") or [])


def test_no_main_push_workflow_cancels_itself_on_ref() -> None:
    """Audit every push-to-main workflow for the #1673 self-cancel pattern.

    A group keyed only on ``github.ref`` combined with a literal
    ``cancel-in-progress: true`` means consecutive main merges cancel each
    other. Guarding cancellation with ``github.ref != 'refs/heads/main'`` or
    keying the group on ``github.sha`` both avoid it.
    """
    offenders: list[str] = []
    workflows = sorted((_REPO_ROOT / ".github" / "workflows").glob("*.yml"))
    assert_that(workflows).is_not_empty()

    for path in workflows:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not _workflow_pushes_to_main(data):
            continue
        scopes: list[tuple[str, Any]] = [(path.name, data)]
        scopes.extend(
            (f"{path.name}:{job_id}", job)
            for job_id, job in (data.get("jobs") or {}).items()
            if isinstance(job, dict)
        )
        for label, scope in scopes:
            concurrency = scope.get("concurrency")
            if not isinstance(concurrency, dict):
                continue
            if concurrency.get("cancel-in-progress") is not True:
                continue
            group = str(concurrency.get("group", ""))
            if "github.sha" in group:
                continue
            if label in _REF_KEYED_PUSH_GROUP_EXEMPTIONS:
                continue
            offenders.append(label)

    assert_that(offenders).is_empty()


# --- Pre-merge dependency vulnerability gate (#1667) ------------------------
#
# The release gate (publish-pypi-on-tag.yml `sbom`) only runs on a version-tag
# push, so a lockfile that trips grype merges green and breaks the publish at a
# ref that can no longer be fixed (v0.91.26 / v0.91.27).
# dependency-vuln-gate.yml runs the same scan pre-merge; these tests pin it to
# the release gate so the two cannot drift, and pin its required-check-safe
# shape (#1196).

_VULN_GATE_WORKFLOW = "dependency-vuln-gate.yml"
_VULN_GATE_JOB = "dependency-vuln-scan"
_VULN_SCAN_ACTION = "lgtm-hq/lgtm-ci/.github/actions/scan-vulnerabilities"
_VULN_SBOM_ACTION = "lgtm-hq/lgtm-ci/.github/actions/generate-sbom"
_VULN_DETECT_ACTION = "lgtm-hq/lgtm-ci/.github/actions/detect-changes"


def _vuln_gate_job() -> dict[str, Any]:
    """Return the pre-merge dependency vulnerability gate job definition.

    Returns:
        The ``dependency-vuln-scan`` job mapping.
    """
    workflow = _load_workflow(name=_VULN_GATE_WORKFLOW)
    return cast(dict[str, Any], workflow["jobs"][_VULN_GATE_JOB])


def _vuln_gate_step(*, uses_prefix: str) -> dict[str, Any]:
    """Return the gate step whose action path equals ``uses_prefix``.

    Matches on the action path before the ``@<ref>`` pin exactly, so a
    similarly named action (e.g. ``scan-vulnerabilities-other@<ref>``) is not
    accepted.

    Args:
        uses_prefix: Full action path (without ``@<ref>``) to match.

    Returns:
        The matching step mapping.
    """
    steps = _vuln_gate_job()["steps"]
    step = next(
        (
            step
            for step in steps
            if str(step.get("uses", "")).partition("@")[0] == uses_prefix
        ),
        None,
    )
    assert_that(step).described_as(
        f"gate step for action {uses_prefix!r}",
    ).is_not_none()
    return cast(dict[str, Any], step)


def test_dependency_vuln_gate_job_exists() -> None:
    """The pre-merge gate job exists and scans the repo dependency set."""
    job = _vuln_gate_job()
    assert_that(job["name"]).contains("Dependency Vulnerability Gate")

    sbom_step = _vuln_gate_step(uses_prefix=_VULN_SBOM_ACTION)
    assert_that(sbom_step["with"]).contains_entry({"target": "."})
    assert_that(sbom_step["with"]).contains_entry({"target-type": "dir"})

    scan_step = _vuln_gate_step(uses_prefix=_VULN_SCAN_ACTION)
    assert_that(scan_step["with"]).contains_entry({"target-type": "sbom"})
    assert_that(str(scan_step["with"]["target"])).contains("steps.sbom.outputs")


def test_dependency_vuln_gate_matches_release_fail_on_threshold() -> None:
    """Pre-merge threshold must equal the release gate's (#1667).

    A pre-merge scan looser than publish-pypi-on-tag.yml's ``sbom`` job
    manufactures false confidence, so the threshold is asserted equal to the
    release gate rather than merely asserted to be ``high``.
    """
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    release_threshold = publish["jobs"]["sbom"]["with"]["fail-on-severity"]

    scan_step = _vuln_gate_step(uses_prefix=_VULN_SCAN_ACTION)

    assert_that(scan_step["with"]["fail-on"]).is_equal_to(release_threshold)


def test_dependency_vuln_gate_shares_release_tooling_ref() -> None:
    """Gate actions are pinned at the release gate's lgtm-ci tooling-ref."""
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    release_ref = str(publish["jobs"]["sbom"]["with"]["tooling-ref"])

    pinned = [
        step["uses"]
        for step in _vuln_gate_job()["steps"]
        if step.get("uses", "").startswith("lgtm-hq/lgtm-ci/")
    ]

    assert_that(pinned).is_not_empty()
    for uses in pinned:
        # Exact equality on the ref after ``@`` — a substring/``contains``
        # check would also pass on a longer ref that merely ends with the
        # release pin, which is the drift this guard exists to forbid.
        ref = uses.split("@", 1)[1]
        assert_that(ref).is_equal_to(release_ref)


def test_dependency_vuln_gate_is_required_check_safe() -> None:
    """The gate must always report its context (#1196).

    A ``paths:`` filter, or a job-level ``if:``, would stop the context from
    ever being created — which deadlocks the merge queue the moment the
    context is added to the ``checks-py-lintro`` ruleset. Path scoping
    therefore lives on the steps inside the job, not on the trigger or the job.
    """
    workflow = _load_workflow(name=_VULN_GATE_WORKFLOW)
    triggers = workflow["on"]

    assert_that(triggers).contains_key(_GITHUB_PULL_REQUEST_EVENT)
    assert_that(triggers).contains_key("merge_group")
    for event in (_GITHUB_PULL_REQUEST_EVENT, "merge_group"):
        assert_that(triggers[event] or {}).does_not_contain_key("paths")
        assert_that(triggers[event] or {}).does_not_contain_key("paths-ignore")

    job = _vuln_gate_job()
    assert_that(job).does_not_contain_key("if")
    # A skipped reusable *caller* collapses its nested contexts, so the gate
    # must stay a plain job that always reports its own check run.
    assert_that(job).does_not_contain_key("uses")
    assert_that(job).contains_key("runs-on")

    # The expensive steps are the ones that skip.
    for prefix in (_VULN_SBOM_ACTION, _VULN_SCAN_ACTION):
        step_if = _normalize_github_expr(_vuln_gate_step(uses_prefix=prefix)["if"])
        assert_that(step_if).contains("steps.changes.outputs.changes")


def test_dependency_vuln_gate_scopes_to_dependency_paths() -> None:
    """The scan covers every language manifest the release gate scans.

    The release gate is ``syft scan dir:.`` over the whole repo, so the
    pre-merge filter must react to any file that can change that graph — not
    just the Python lock — or it is looser than the release gate (#1667). This
    repo's SBOM is cataloged from JavaScript, Python, Rust and Go manifests,
    so all four families must be watched, at any depth.
    """
    detect_step = _vuln_gate_step(uses_prefix=_VULN_DETECT_ACTION)
    filters = yaml.safe_load(detect_step["with"]["filters"])
    deps = filters["deps"]

    assert_that(filters).contains_key("deps")
    # Python — `*requirements*.txt` basenames ("requirements" anywhere, e.g.
    # dev-requirements.txt) and the requirements/ dir layout at any depth.
    assert_that(deps).contains("**/uv.lock")
    assert_that(deps).contains("**/pyproject.toml")
    assert_that(deps).contains("**/*requirements*.txt")
    assert_that(deps).contains("**/requirements/*.txt")
    assert_that(deps).contains("**/requirements/**/*.txt")
    # JavaScript / TypeScript (root bun.lock, apps/site, npm/ manifests)
    assert_that(deps).contains("**/package.json")
    assert_that(deps).contains("**/bun.lock")
    # Rust and Go (test_samples manifests are in the scanned tree)
    assert_that(deps).contains("**/Cargo.lock")
    assert_that(deps).contains("**/Cargo.toml")
    assert_that(deps).contains("**/go.mod")
    assert_that(deps).contains("**/go.sum")
    # The gate exercises itself and the real release gate.
    assert_that(deps).contains(".github/workflows/publish-pypi-on-tag.yml")


def test_dependency_vuln_gate_filter_is_pure_allow_list() -> None:
    """The ``deps`` filter carries no ``!`` negations (dorny ``some`` trap).

    dorny/paths-filter defaults to ``predicate-quantifier: some`` and the
    lgtm-ci wrapper does not override it, so a standalone negation like
    ``!**/node_modules/**`` would match every file *outside* node_modules and
    force ``deps`` true on nearly every PR — it does not subtract. Excluding
    vendored trees would also make this gate looser than the release gate,
    which scans them via ``syft scan dir:.``. So the filter must stay a pure
    allow-list.
    """
    detect_step = _vuln_gate_step(uses_prefix=_VULN_DETECT_ACTION)
    patterns = yaml.safe_load(detect_step["with"]["filters"])["deps"]

    negations = [p for p in patterns if p.startswith("!")]
    assert_that(negations).is_empty()


def test_dependency_vuln_gate_filter_globs_match_committed_manifests() -> None:
    """Every committed dependency manifest matches the gate's ``deps`` filter.

    Guards the #1667 drift concern directly: if a real manifest the release
    gate scans is not matched by any ``deps`` pattern, a PR could change the
    scanned graph through it without the pre-merge gate reacting. Enumerates
    the repo's tracked manifests and asserts each matches a pattern, using the
    same picomatch-style semantics dorny applies (pure allow-list, ``some``
    quantifier — a file matches the filter if it matches any pattern).
    """
    detect_step = _vuln_gate_step(uses_prefix=_VULN_DETECT_ACTION)
    patterns = yaml.safe_load(detect_step["with"]["filters"])["deps"]

    tracked = subprocess.run(  # nosec B603 B607 - fixed argv against this repo
        ["git", "ls-files"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()

    # Genuine dependency-manifest basenames the release gate's syft scan
    # catalogs. Deliberately excludes ``setup.py`` / ``setup.cfg``: the filter
    # still lists them for future-proofing, but this repo's only committed
    # ``setup.py`` is a Click command module (lintro/cli_utils/commands/), not
    # packaging metadata, and syft does not catalog it — so it is not a manifest
    # this drift guard should assert on.
    manifest_names = {
        "pyproject.toml",
        "uv.lock",
        "poetry.lock",
        "Pipfile",
        "Pipfile.lock",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "bun.lock",
        "bun.lockb",
        "Cargo.toml",
        "Cargo.lock",
        "go.mod",
        "go.sum",
    }

    # Match with pathspec's gitignore semantics rather than a hand-rolled
    # matcher. gitignore globs support the ``**`` globstar natively, closely
    # mirroring the picomatch semantics dorny/paths-filter applies: a leading
    # ``**/`` matches at any depth including the repo root (so ``**/uv.lock``
    # matches both ``uv.lock`` and ``a/b/uv.lock``), and ``*`` within a segment
    # does not cross ``/``. The filter is a pure allow-list, so a manifest is
    # covered iff it matches at least one pattern (``GitIgnoreSpec.match_file``).
    spec = GitIgnoreSpec.from_lines(patterns)

    manifests = [p for p in tracked if Path(p).name in manifest_names]
    # requirements files are a glob family: `*requirements*.txt` basenames, and
    # any `.txt` under a `requirements/` directory at any depth
    # (requirements/base.txt, requirements/dev/base.txt).
    manifests += [
        p
        for p in tracked
        if p.endswith(".txt")
        and ("requirements" in Path(p).name or "requirements" in Path(p).parent.parts)
    ]
    assert_that(manifests).is_not_empty()

    unmatched = [path for path in manifests if not spec.match_file(path)]

    assert_that(unmatched).is_empty()


def _js_regex_to_python(pattern: str) -> str:
    """Translate a JavaScript regex to Python's named-group spelling.

    Renovate config is JS-flavoured: named groups are ``(?<name>`` where Python
    spells them ``(?P<name>``. Lookbehind assertions (``(?<=``, ``(?<!``) share
    the ``(?<`` prefix but are identical in both flavours, so they must be left
    alone — rewriting them yields invalid Python syntax.
    """
    return re.sub(r"\(\?<(?![=!])", "(?P<", pattern)


def _renovate_pinned_image_manager() -> dict[str, Any]:
    """Return the customManager governing the pinned py-lintro release image."""
    config = json.loads(
        (_REPO_ROOT / "renovate.json").read_text(encoding="utf-8"),
    )
    covering = [
        manager
        for manager in config.get("customManagers", [])
        if any(
            "py-lintro" in match_string
            for match_string in manager.get("matchStrings", [])
        )
    ]
    assert_that(covering).is_length(1)
    return cast(dict[str, Any], covering[0])


# Every workflow file carrying a pinned release reference, and how many sites
# it must carry. Hard-coding the counts is deliberate: asserting only that the
# surviving references agree would stay green if a refactor deleted all but one
# pin, which is exactly the drift this guard exists to catch (#1751).
_PINNED_IMAGE_SITES = {
    "dogfood-nightly.yml": 3,
    "docker-ci.yml": 4,
}


def test_pinned_release_image_sites_share_one_reference() -> None:
    """Every pinned py-lintro release site must name the same release.

    The nightly dogfood run and the docker-ci fork-PR fallback pin a released
    ``py-lintro`` image by digest. The pin is deliberately frozen so the
    digest-lag gate in ``scripts/ci/verify-image-manifest-tools.sh`` stays
    meaningful, and Renovate bumps every site as one set (#1751). A partial
    bump would leave the two workflows linting with different images while
    both claim to use "the pinned release" — this asserts that cannot happen.

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


_COSIGN_SIGN_SCRIPT = _REPO_ROOT / "scripts" / "ci" / "cosign-sign-images.sh"


def _auto_rerun_signatures() -> list[str]:
    """Return the extra signatures wired into the auto-rerun matcher.

    Returns:
        The non-blank lines of the ``signatures`` input passed to the
        reusable auto-rerun workflow, one fixed-string signature per line.
    """
    workflow = _load_workflow(name="auto-rerun-on-infra-failure.yml")
    signatures = str(workflow["jobs"]["rerun"]["with"]["signatures"])
    return [line.strip() for line in signatures.splitlines() if line.strip()]


def _cosign_oidc_flake_markers() -> list[str]:
    """Return the fixed-string markers the in-step cosign retry keys off.

    Returns:
        The entries of the ``oidc_flake_markers`` bash array declared in
        ``scripts/ci/cosign-sign-images.sh``.
    """
    script = _COSIGN_SIGN_SCRIPT.read_text(encoding="utf-8")
    # Terminate on the array's own closing line (``)`` alone) rather than the
    # first ``)`` character: markers are fixed strings under ``grep -F`` and may
    # legitimately contain parentheses, e.g. ``getting cert (403)``. A
    # character-class scan would truncate there and silently drop the rest,
    # letting the parity test pass while a marker is missing from the workflow.
    array_match = re.search(
        r"^oidc_flake_markers=\((.*?)^\)",
        script,
        re.DOTALL | re.MULTILINE,
    )
    assert_that(
        array_match,
        description="oidc_flake_markers array not found",
    ).is_not_none()
    assert array_match is not None  # narrow for mypy
    return re.findall(r'"([^"]+)"', array_match.group(1))


def test_auto_rerun_passes_cosign_oidc_flake_signatures() -> None:
    """The auto-rerun matcher must know the cosign ambient-OIDC signatures.

    #1646 retries the transient OIDC token-fetch flake in-step; when that
    bounded retry is exhausted the signing job still fails and the publish
    run needs a run-level re-run. The auto-rerun safety net only fires when
    the failed-job logs match a known signature, so the reusable matcher
    receives the cosign flake markers via its ``signatures`` input (#1689).
    Upstream matches with ``grep -qF``, so these must stay fixed strings.
    """
    signatures = _auto_rerun_signatures()

    assert_that(signatures).contains("fetching ambient OIDC credentials")
    assert_that(signatures).contains("retrieving ID token")
    assert_that(signatures).contains("reading ID token")


def test_auto_rerun_signatures_cover_in_step_retry_markers() -> None:
    """Every in-step cosign retry marker must also reach the auto-rerun net.

    The safety net inspects the final failed attempt's logs, which contain
    whichever marker the retry loop in scripts/ci/cosign-sign-images.sh
    matched. A marker missing from the workflow ``signatures`` input would
    leave that exhausted-retry failure mode needing a manual backfill.
    """
    signatures = _auto_rerun_signatures()
    markers = _cosign_oidc_flake_markers()

    assert_that(markers).is_not_empty()
    assert_that(markers).is_subset_of(signatures)


# Constructs that only make sense if the author believed the signature was a
# regex. Bare metacharacters are deliberately NOT listed: ``grep -qF`` compares
# literally, so parentheses, brackets and plus signs are ordinary text and
# occur naturally in tool output (``getting cert (403)``). Rejecting those
# would force future markers to diverge from the log lines they must match.
_REGEX_INTENT_TELLS = (
    r"^\^",  # leading anchor
    r"\$$",  # trailing anchor
    r"\.\*",  # .*
    r"\.\+",  # .+
    r"\\[dwsb]",  # \d \w \s \b
    r"\(\?",  # (?: (?= (?<
    r"\[[^\]]*-[^\]]*\]",  # character class with a range, e.g. [0-9]
)


def test_auto_rerun_signatures_are_fixed_strings() -> None:
    """Extra signatures must be plain fixed strings, not regexes.

    ``rerun-on-infra-failure.sh`` matches with ``grep -qF``, so a regex or
    an anchor would be compared literally and silently never match. The check
    targets constructs that betray regex *intent* rather than any
    metacharacter, since literal punctuation is legitimate under ``-F``.
    """
    signatures = _auto_rerun_signatures()

    assert_that(signatures).is_not_empty()
    for signature in signatures:
        for tell in _REGEX_INTENT_TELLS:
            assert_that(re.search(tell, signature)).described_as(
                f"{signature!r} looks like a regex ({tell})",
            ).is_none()


def test_auto_rerun_matches_docker_hub_buildx_pull_timeout() -> None:
    """The matcher must know the Docker Hub buildkit-pull timeout.

    `Setup Docker Buildx` boots buildkit by pulling moby/buildkit from
    Docker Hub. When Docker Hub is slow the daemon times out and the job
    dies before doing any real work -- purely transient, and not a
    harden-runner block (registry-1.docker.io:443 is already allowed).
    None of lgtm-ci's default signatures match it, so the v0.91.42 release
    run (30148763859, Merge Manifests job 89692290242) was never
    auto-rerun. The signature is scoped to the registry URL rather than a
    bare "context deadline exceeded", which would absorb genuine timeouts
    elsewhere that deserve a human.
    """
    signatures = _auto_rerun_signatures()

    assert_that(signatures).contains(
        'Get "https://registry-1.docker.io/v2/": context deadline exceeded',
    )
    # A bare timeout string is too broad to auto-rerun on.
    assert_that(signatures).does_not_contain("context deadline exceeded")


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


def test_code_quality_gate_does_not_consume_the_timeout_verdict() -> None:
    """The timeout verdict must stay diagnostic, never a gate input.

    ``dogfood-skip-gate`` always lints the full repo, so its verdict is not
    evidence about the authoritative lint run: under ``lint-scope == 'changed'``
    that run lints only changed files, and a tool that times out reports zero
    findings precisely because it did not finish. Wiring the verdict into the
    gate lets a genuine finding be absorbed and the required check turn green.

    A sound implementation needs the authoritative run's own structured report,
    which the upstream reusable lint workflow does not publish (lgtm-ci#746).
    """
    docker_ci = _load_workflow(name="docker-ci.yml")
    gate_job = docker_ci["jobs"]["code-quality-gate"]

    assert_that(gate_job["needs"]).does_not_contain("dogfood-skip-gate")

    gate_step = next(step for step in gate_job["steps"] if step.get("id") == "gate")
    assert_that(gate_step.get("env") or {}).does_not_contain_key("TIMEOUT_FLAKE")


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
    ):
        assert_that(sparse).contains(script)


# --- Release version-skew audit wiring (#1712) ------------------------------

_SKEW_WORKFLOW = "release-version-skew-audit.yml"
_SKEW_SCRIPT = "scripts/ci/check-release-version-skew.py"


def test_version_skew_audit_runs_on_schedule_and_dispatch() -> None:
    """The skew audit is a scheduled backstop, also runnable on demand.

    Propagation lag means the settle window matters more than immediacy, so
    the alarm runs as a periodic audit rather than inline in the release run.
    """
    workflow = _load_workflow(name=_SKEW_WORKFLOW)
    triggers = workflow["on"]
    assert_that(triggers).contains_key("schedule")
    assert_that(triggers).contains_key("workflow_dispatch")
    assert_that(triggers["schedule"]).is_not_empty()
    assert_that(workflow["permissions"]).is_equal_to({})


def test_version_skew_audit_invokes_the_checked_in_script() -> None:
    """The audit job calls the dedicated script, not inline shell logic."""
    workflow = _load_workflow(name=_SKEW_WORKFLOW)
    steps = workflow["jobs"]["audit"]["steps"]
    run_steps = [step for step in steps if "run" in step]
    assert_that(run_steps).is_length(1)
    assert_that(run_steps[0]["run"]).contains(_SKEW_SCRIPT)
    assert_that((_REPO_ROOT / _SKEW_SCRIPT).exists()).is_true()
    # Alarm, not gate: the audit must not be wired into any release job's
    # ``needs:`` chain, and must not carry write permissions.
    assert_that(workflow["jobs"]["audit"]["permissions"]).is_equal_to(
        {"contents": "read"},
    )


def test_version_skew_audit_notifies_via_deduplicated_notifier() -> None:
    """Skew alarms ping one deduplicated issue instead of one issue per run."""
    workflow = _load_workflow(name=_SKEW_WORKFLOW)
    notify = workflow["jobs"]["notify-failure"]
    assert_that(notify["needs"]).contains("audit")
    assert_that(notify["uses"]).contains("reusable-main-failure-notifier.yml")
    assert_that(notify["with"]["workflow-key"]).is_equal_to("release-version-skew")
    # Main-only: a dispatch from a feature branch must not open the issue.
    assert_that(_normalize_github_expr(notify["if"])).contains(
        "github.ref == 'refs/heads/main'",
    )


def test_version_skew_audit_allows_every_channel_endpoint() -> None:
    """Egress policy allows exactly the hosts the audit must reach."""
    workflow = _load_workflow(name=_SKEW_WORKFLOW)
    steps = workflow["jobs"]["audit"]["steps"]
    harden = next(
        step for step in steps if str(step.get("uses", "")).startswith("step-security/")
    )
    assert_that(harden["with"]["egress-policy"]).is_equal_to("block")
    allowed = harden["with"]["allowed-endpoints"].split()
    for endpoint in (
        "pypi.org:443",
        "registry.npmjs.org:443",
        "raw.githubusercontent.com:443",
        "api.github.com:443",
    ):
        assert_that(allowed).contains(endpoint)


def test_ghcr_cleanup_sweeps_commit_tags_with_the_shared_safety_rule() -> None:
    """Per-commit tags must be swept, reusing the ci-* sweeper's guardrails.

    Unbounded ``sha-<commit>`` growth is not only storage: the tag list is what
    Renovate's docker datasource enumerates to find newer versions, and past
    1000 tags the first page held no recent release at all (#1590).

    The sweep must go through ``sweep-ci-ghcr-tags.sh`` rather than a bespoke
    deletion path, because that script only deletes versions whose *every* tag
    matches the prefix — so a release carrying both ``sha-<commit>`` and a
    version tag can never be removed by it.
    """
    cleanup = _load_workflow(name="ghcr-cleanup.yml")
    jobs = cleanup["jobs"]
    assert_that(jobs).contains_key("sweep-sha-tags")

    job = jobs["sweep-sha-tags"]
    sweep_steps = [
        step
        for step in job["steps"]
        if step.get("run") == "scripts/ci/maintenance/sweep-ci-ghcr-tags.sh"
    ]
    assert_that(sweep_steps).is_length(1)
    assert_that(sweep_steps[0]["env"]["TAG_PREFIX"]).is_equal_to("sha-")
    assert_that(job["permissions"]["packages"]).is_equal_to("write")
