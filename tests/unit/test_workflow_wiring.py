"""Contract tests for critical GitHub Actions workflow wiring."""

from __future__ import annotations

import ast
import re
import subprocess  # nosec B404 - subprocess runs fixed git argv against this repo
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
                # Full-scope scenarios: the changed-files job did not run.
                "dogfooding-lint-changed": "skipped",
            },
            outputs={
                "dogfooding-lint": lint_outputs,
                "dogfooding-lint-changed": {"exit-code": "", "status": ""},
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
            },
            outputs={
                "dogfooding-lint": {"exit-code": "", "status": ""},
                "dogfooding-lint-changed": changed_outputs,
            },
            event_is_pull_request=True,
            head_repo_not_fork=True,
            pull_request_not_draft=True,
        ),
    ).is_equal_to(expected)


def test_docker_ci_lintro_code_quality_wires_upstream_jobs() -> None:
    """Required check propagates docker-build failure even when lint is skipped."""
    docker_ci = _load_workflow(name="docker-ci.yml")
    job = docker_ci["jobs"]["lintro-code-quality"]

    assert_that(job["needs"]).contains(
        "changes",
        "docker-build",
        "manifest-sync",
        "dogfooding-lint",
        "dogfooding-lint-changed",
    )
    assert_that(job["if"]).contains("!cancelled()")
    upstream = _normalize_github_expr(job["with"]["upstream-result"])
    assert_that(upstream).is_equal_to(
        _normalize_github_expr(
            "${{ needs.changes.outputs.pipeline == 'false' && 'success' "
            "|| needs.docker-build.result != 'success' && needs.docker-build.result "
            "|| ( needs.manifest-sync.result != 'success' && "
            "needs.manifest-sync.result != 'skipped' ) && needs.manifest-sync.result "
            "|| needs.changes.outputs.lint-scope == 'changed' && "
            "needs.dogfooding-lint-changed.result "
            "|| needs.dogfooding-lint.result }}",
        ),
    )
    status_output = _normalize_github_expr(job["with"]["status-output"])
    assert_that(status_output).is_equal_to(
        _normalize_github_expr(
            "${{ needs.changes.outputs.lint-scope == 'changed' && "
            "needs.dogfooding-lint-changed.outputs.status "
            "|| needs.dogfooding-lint.outputs.status }}",
        ),
    )


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


# Canonical lgtm-ci pin used by all py-lintro workflows (v0.52.4).
# Pages deploy must not regress to v0.32.3 (missing GH_TOKEN in bundler).
# The 40-hex git SHA trips trufflehog's Github legacy-token detector under
# --no-verification; it is a commit pin, not a credential.
_LGTM_CI_PIN = "ee8484ca71db3a2c2c33da6128bbf2330fcd7c88"  # trufflehog:ignore


def test_all_lgtm_ci_refs_use_the_canonical_pin() -> None:
    """Every lgtm-ci ref in workflows must match the single canonical pin.

    Guards the repo-wide invariant from #1280: `uses:` refs,
    `tooling-ref:` inputs, and manual `actions/checkout` steps targeting
    lgtm-hq/lgtm-ci all point at the same lgtm-ci commit, so pins cannot
    silently drift apart again. Any ref shape (tag, branch, short SHA,
    any quoting) that is not the canonical pin is an offender.
    """
    ref_pattern = re.compile(
        r"lgtm-hq/lgtm-ci/[^@\s]+@([^\s#]+)|tooling-ref:\s*[\"']?([^\"'\s#]+)",
    )
    workflows_dir = _REPO_ROOT / ".github" / "workflows"
    offenders: list[str] = []
    workflow_paths = sorted(
        (*workflows_dir.glob("*.yml"), *workflows_dir.glob("*.yaml")),
    )
    for path in workflow_paths:
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            for match in ref_pattern.finditer(line):
                ref = match.group(1) or match.group(2)
                if ref != _LGTM_CI_PIN:
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
                if with_block.get("ref") != _LGTM_CI_PIN:
                    offenders.append(
                        f"{path.name}:{job_id}: checkout ref "
                        f"{with_block.get('ref')!r}",
                    )

    assert_that(offenders).is_empty()


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
    workflow = _load_workflow(name="deploy-pages.yml")
    deploy = workflow["jobs"]["deploy"]
    uses = deploy["uses"]
    tooling_ref = deploy["with"]["tooling-ref"]

    assert_that(uses).contains(_LGTM_CI_PIN)
    assert_that(uses).contains("reusable-deploy-site-with-reports.yml")
    assert_that(tooling_ref).contains(_LGTM_CI_PIN)
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
    # Verifies the same pinned release digest the nightly dogfood run lints with.
    assert_that(verify_steps[0]["env"]["IMAGE"]).contains(
        "ghcr.io/lgtm-hq/py-lintro@sha256:",
    )

    # A pinned-digest failure must reach the deduplicated failure notifier.
    assert_that(jobs["notify-failure"]["needs"]).contains("verify-pinned-image-tools")


def test_publish_pypi_sbom_fails_on_high_severity() -> None:
    """Release SBOM must gate publishes on high/critical vulns (#1118)."""
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    sbom = publish["jobs"]["sbom"]
    assert_that(sbom["with"]).contains_entry({"fail-on-severity": "high"})
    assert_that(sbom["with"].get("scan-vulnerabilities")).is_true()
