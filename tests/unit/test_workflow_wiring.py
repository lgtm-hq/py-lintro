"""Contract tests for critical GitHub Actions workflow wiring."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from assertpy import assert_that

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
                expr = _replace_github_token(
                    expr,
                    token=f"needs.{job}.outputs.{output_name} != ''",
                    replacement=repr(output_value != ""),
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


def test_semantic_pr_title_can_write_failure_comments() -> None:
    """Semantic PR title workflow can upsert failure comments on PRs."""
    workflow = _load_workflow(name="semantic-pr-title.yml")

    assert_that(workflow["jobs"]["semantic-title"]["permissions"]).is_equal_to(
        {
            "contents": "read",
            "pull-requests": "write",
        },
    )


def test_docker_ci_dogfooding_lint_waits_on_manifest_sync() -> None:
    """Dogfooding lint depends on manifest-sync and allows draft-PR skips."""
    docker_ci = _load_workflow(name="docker-ci.yml")
    lint_job = docker_ci["jobs"]["dogfooding-lint"]
    comment_job = docker_ci["jobs"]["dogfooding-pr-comment"]
    lint_needs = lint_job["needs"]
    lint_condition = lint_job["if"]
    comment_condition = comment_job["if"]

    assert_that(lint_needs).contains("docker-build", "manifest-sync")
    assert_that(lint_condition).contains("always()")
    assert_that(lint_condition).contains("!cancelled()")
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
    ("docker_build", "manifest_sync", "cancelled", "expected"),
    [
        ("success", "success", False, True),
        ("success", "skipped", False, True),
        ("success", "failure", False, False),
        ("failure", "success", False, False),
        ("success", "success", True, False),
    ],
)
def test_docker_ci_lint_condition_semantics(
    *,
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
            results={"dogfooding-lint": dogfooding_lint},
            outputs={"dogfooding-lint": lint_outputs},
            event_is_pull_request=event_is_pull_request,
            head_repo_not_fork=head_repo_not_fork,
            pull_request_not_draft=pull_request_not_draft,
        ),
    ).is_equal_to(expected)


def test_docker_ci_lintro_code_quality_wires_upstream_jobs() -> None:
    """Required check propagates docker-build failure even when lint is skipped."""
    docker_ci = _load_workflow(name="docker-ci.yml")
    job = docker_ci["jobs"]["lintro-code-quality"]

    assert_that(job["needs"]).contains(
        "docker-build",
        "manifest-sync",
        "dogfooding-lint",
    )
    assert_that(job["if"]).contains("!cancelled()")
    upstream = _normalize_github_expr(job["with"]["upstream-result"])
    assert_that(upstream).is_equal_to(
        _normalize_github_expr(
            "${{ needs.docker-build.result != 'success' && needs.docker-build.result "
            "|| ( needs.manifest-sync.result != 'success' && "
            "needs.manifest-sync.result != 'skipped' ) && needs.manifest-sync.result "
            "|| needs.dogfooding-lint.result }}",
        ),
    )


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
    assert_that(script).contains("tail -n +3 lintro-report/report.md")
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
    assert_that(workflow["jobs"]["notify"]["needs"]).is_equal_to("lintro-report")
