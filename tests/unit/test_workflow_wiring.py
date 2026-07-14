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


def test_version_pr_formats_changelog_via_dedicated_script() -> None:
    """Version-PR workflow reflows the generated CHANGELOG via a repo script."""
    version_pr = _load_workflow(name="release-version-pr.yml")

    script = version_pr["jobs"]["version-pr"]["with"]["version-update-script"]
    assert_that(script).is_equal_to("scripts/ci/format-changelog.py")
    assert_that((_REPO_ROOT / script).is_file()).is_true()


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
    assert publish_step is not None
    assert_that(publish_step["env"]["NPM_DIST_TAG"]).contains("inputs.dist_tag")


# Canonical lgtm-ci pin used by all py-lintro workflows (v0.52.4).
# Pages deploy must not regress to v0.32.3 (missing GH_TOKEN in bundler).
_LGTM_CI_PIN = "768a6b72f0a5346b5ecba3f4e13b90040472341c"


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
