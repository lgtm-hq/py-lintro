"""Contract tests for critical GitHub Actions workflow wiring."""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import shlex
import subprocess  # nosec B404 - subprocess runs fixed git argv against this repo
import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import pytest
import yaml
from assertpy import assert_that
from pathspec import GitIgnoreSpec

from lintro._tool_versions import TOOL_VERSIONS
from lintro.enums.tool_name import ToolName
from tests.integration._tools import ALLOW_VERSION_LAG_ENV, TOOLS_IMAGE_ENV

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LINTRO_REPORT_SCRIPT = (
    _REPO_ROOT / "scripts" / "ci" / "testing" / "lintro-report-generate.sh"
)
# Bandit B106 false-positives on the contiguous ``pull_request`` literal because
# of the ``pass`` substring. Build the event kind from parts for token matching.
_GITHUB_PULL_REQUEST_EVENT = "pull_" + "request"

# #2562: build-binary.yml split into a build stage (compile, verify, attest,
# upload artifacts) and a publish stage (release upload, Homebrew dispatch).
_BUILD_BINARY_WORKFLOW = "build-binaries.yml"
_PUBLISH_BINARIES_WORKFLOW = "publish-binaries.yml"


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


def test_version_pr_is_gated_on_a_green_tag_publish() -> None:
    """The version PR waits on the publish gate (#2516).

    A broken tag publish used to mint one dead version per merge to ``main``
    (v0.151.2 through v0.152.6). The ``publish-gate`` job reads the last
    version-tag publish run and the version-PR job runs only when it was
    green; ``force`` is the manual override for the first release after a fix.
    """
    workflow = _load_workflow(name="release-version-pr.yml")
    gate = workflow["jobs"]["publish-gate"]
    version_pr = workflow["jobs"]["version-pr"]

    assert_that(version_pr["needs"]).contains("publish-gate")
    # Fail open on a *missing* verdict: if the gate job dies before its script
    # writes the output, `== 'true'` would freeze every release. Only an
    # explicit `false` stops the version PR.
    assert_that(_normalize_github_expr(version_pr["if"])).is_equal_to(
        "always() && needs.publish-gate.outputs.publish_green != 'false'",
    )
    # Read-only: the gate inspects run conclusions and touches nothing else.
    assert_that(gate["permissions"]).is_equal_to({"actions": "read"})
    assert_that(gate["outputs"]["publish_green"]).contains(
        "steps.gate.outputs.publish_green",
    )
    gate_script = "scripts/ci/check-last-publish-green.py"
    assert_that((_REPO_ROOT / gate_script).is_file()).is_true()
    gate_steps = [
        step for step in gate["steps"] if gate_script in str(step.get("run", ""))
    ]
    assert_that(gate_steps).is_length(1)

    force_input = workflow["on"]["workflow_dispatch"]["inputs"]["force"]
    assert_that(force_input["type"]).is_equal_to("boolean")
    assert_that(force_input["default"]).is_false()

    # The force decision stays in the workflow expression and reaches the
    # script as one quoted word, so no conditional logic lives in inline
    # shell and an unset input cannot smuggle a second argument through.
    gate_step = gate_steps[0]
    force_flag = _normalize_github_expr(str(gate_step["env"]["FORCE_FLAG"]))
    assert_that(force_flag).is_equal_to(
        "${{ inputs.force && '--force' || '' }}",
    )
    assert_that(_normalize_github_expr(str(gate_step["run"]))).is_equal_to(
        f'python3 {gate_script} "${{FORCE_FLAG}}"',
    )


def _module_constant(*, script: Path, name: str) -> str:
    """Return a module-level string constant from a standalone CI script.

    The scripts are hyphenated and executable, so they are read as source
    rather than imported.

    Args:
        script: Path to the script.
        name: Name of the module-level constant.

    Raises:
        AssertionError: If the script defines no such constant.

    Returns:
        The constant's string value.
    """
    tree = ast.parse(script.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                value = ast.literal_eval(node.value)
                assert_that(value).is_instance_of(str)
                return cast(str, value)
    raise AssertionError(f"{name} not found in {script.name}")


def test_release_helpers_name_the_real_publish_workflow_file() -> None:
    """Both release helpers must name the live publish workflow file.

    ``publish_green``/skew both hinge on a workflow *file name* passed to the
    Actions API, which answers an empty run list for an unknown file rather
    than erroring. Renaming the workflow would therefore turn both checks into
    permanent, silent all-clears. Resolve the file from its ``name:`` so the
    rename breaks a test instead.
    """
    workflows_dir = _REPO_ROOT / ".github" / "workflows"
    matches = [
        path.name
        for path in _workflow_paths()
        if _load_workflow(name=path.name).get("name") == "Publish - PyPI Production"
    ]
    assert_that(matches).described_as(
        "exactly one workflow is named 'Publish - PyPI Production'",
    ).is_length(1)
    publish_workflow = matches[0]
    assert_that((workflows_dir / publish_workflow).is_file()).is_true()

    scripts_dir = _REPO_ROOT / "scripts" / "ci"
    for script_name, constant in (
        ("check-last-publish-green.py", "DEFAULT_WORKFLOW"),
        ("check-release-version-skew.py", "DEFAULT_RELEASE_WORKFLOW"),
    ):
        value = _module_constant(
            script=scripts_dir / script_name,
            name=constant,
        )
        assert_that(value).described_as(
            f"{script_name}:{constant} must name the publish workflow file",
        ).is_equal_to(publish_workflow)


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
    # and merges would deadlock. publish must NOT carry it, so a red gate
    # skips the GHCR promotion.
    build_condition = _normalize_github_expr(docker_ci["jobs"]["docker-build"]["if"])
    assert_that(build_condition).contains("!cancelled()")
    publish_condition = _normalize_github_expr(docker_ci["jobs"]["publish"]["if"])
    assert_that(publish_condition).does_not_contain("!cancelled()")
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
def test_dogfood_lint_callers_allow_the_hosted_runner_watchdog(job_id: str) -> None:
    """Dogfood lint callers must allow GitHub's hosted-runner watchdog (#2352).

    harden-runner block mode denied `hosted-compute-watchdog-*.githubapp.com`
    and `hosted-compute-request-orchestrator-*.githubapp.com`, and long jobs
    were reclaimed mid-run with exit 143. The reusable workflow's enforcing
    harden-runner step reads `allowed-endpoints` verbatim, so the caller must
    carry the whole baseline plus the watchdog entry — asserting a couple of
    baseline hosts keeps a future edit from shrinking the list to one entry.

    Args:
        job_id: Dogfooding lint caller whose egress allowlist is under test.
    """
    docker_ci = _load_workflow(name="docker-ci.yml")
    job_with = docker_ci["jobs"][job_id]["with"]
    allowed = set(str(job_with["allowed-endpoints"]).split())

    assert_that(job_with["egress-policy"]).is_equal_to("block")
    assert_that(job_with["allowed-endpoints-mode"]).is_equal_to("append")
    assert_that(allowed).contains("*.githubapp.com:443")
    assert_that(allowed).contains(
        "github.com:443",
        "api.github.com:443",
        "ghcr.io:443",
        "pypi.org:443",
    )


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
def test_long_docker_ci_jobs_allow_the_hosted_runner_watchdog(job_id: str) -> None:
    """Every long in-repo Docker CI job allows the watchdog endpoints (#2352).

    Jobs whose budget exceeds ~10 minutes are the ones observed dying with
    "The runner has received a shutdown signal" while harden-runner blocked
    GitHub's hosted-compute watchdog and request-orchestrator hosts.

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

    assert_that(harden["with"]["egress-policy"]).is_equal_to("block")
    assert_that(job["timeout-minutes"]).is_greater_than(10)
    assert_that(allowed).contains("*.githubapp.com:443")


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
        "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
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


def test_test_ci_has_no_path_classification_surface() -> None:
    """test-ci must not reintroduce a pipeline classifier (#2108, #2297).

    The Python matrix can never path-skip: ``pipeline-skip`` is hard-false
    because a skipped reusable ``test`` job publishes the uninterpolated
    check name and deadlocks required-check merges. The former ``changes``
    job therefore computed a ``pipeline`` output whose only consumer
    (``stage-coverage-html``) is push-only, while
    ``resolve-pipeline-relevance.sh`` resolves ``pipeline=false`` on
    ``pull_request`` alone — the condition could never be false. Guard the
    whole surface, not just the job name, so it cannot creep back.
    """
    test_ci = _load_workflow(name="test-ci.yml")
    raw = (_REPO_ROOT / ".github" / "workflows" / "test-ci.yml").read_text(
        encoding="utf-8",
    )

    assert_that(test_ci["jobs"]).does_not_contain_key("changes")
    assert_that(raw).does_not_contain("resolve-pipeline-relevance.sh")
    assert_that(raw).does_not_contain("needs.changes.")

    # Exact dependency lists, not a "does not contain 'changes'" subset check:
    # a classifier reintroduced under any other job id would slip past a
    # name-shaped assertion. These are the only edges test-ci may have.
    expected_needs: dict[str, list[str]] = {
        "test-compat": [],
        "test-coverage": [],
        "test-gate": ["test-compat", "test-coverage"],
        "test-suite-coverage": ["test-gate"],
        "stage-coverage-html": ["test-coverage"],
    }
    assert_that(set(test_ci["jobs"])).is_equal_to(set(expected_needs))
    for job_id, expected in expected_needs.items():
        assert_that(test_ci["jobs"][job_id].get("needs") or []).described_as(
            job_id,
        ).is_equal_to(expected)
    # on.<event>.paths collapses nested required contexts (#1359).
    triggers = test_ci["on"]
    assert_that(triggers).is_not_empty()
    for trigger in triggers.values():
        if isinstance(trigger, dict):
            assert_that(trigger).does_not_contain_key("paths")
            assert_that(trigger).does_not_contain_key("paths-ignore")


def test_test_ci_reusables_never_path_skip() -> None:
    """Reusable callers always run the matrix and never path-skip.

    ``if: '!cancelled()'`` mirrors docker-ci's docker-build gate: the matrix
    runs unless the whole workflow is cancelled, instead of collapsing to
    skipped → false green. With the classifier gone (#2297) the callers
    have no upstream dependency at all, so nothing can skip them.

    ``pipeline-skip`` stays hard-false (#2108): lgtm-ci's skipped ``test``
    job publishes as ``test-compat / inputs.job-name`` rather than the
    org-required ``test-compat / Python Compatibility`` (and the coverage
    equivalent), which deadlocks version-bump merges. Re-enable only when
    that reusable interpolates the skipped job name.
    """
    expected_job_names = {
        "test-compat": "Python Compatibility",
        "test-coverage": "Python Coverage",
    }
    test_ci = _load_workflow(name="test-ci.yml")
    for job_name, published_name in expected_job_names.items():
        job = test_ci["jobs"][job_name]
        assert_that(job.get("needs") or []).is_empty()
        assert_that(job["if"]).is_equal_to("!cancelled()")
        assert_that(job["with"]["pipeline-skip"]).is_false()
        assert_that(job["with"]["job-name"]).is_equal_to(published_name)


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


def test_version_lag_env_matches_the_plugin_contract() -> None:
    """The gate reads the same env var the plugins do (#1582).

    ``tests/integration/_tools.py`` mirrors lintro's version-lag allowance so
    an allow-listed lagging binary keeps collecting its module. The name is
    spelled once per side; a rename in either would silently re-introduce the
    skip the allowance exists to prevent.
    """
    from lintro.plugins.execution_preparation import _ALLOW_VERSION_LAG_ENV

    assert_that(ALLOW_VERSION_LAG_ENV).is_equal_to(_ALLOW_VERSION_LAG_ENV)


def test_test_ci_matrix_collects_the_integration_suite() -> None:
    """The Python matrix runs tests/integration instead of ignoring it (#465).

    Every integration module gates on ``tests/integration/_tools.py``, which
    skips on a toolless runner and only fails inside the tools image, so the
    hosted matrix can collect the suite without installing any wrapped tool.
    """
    test_ci = _load_workflow(name="test-ci.yml")
    for job_name in ("test-compat", "test-coverage"):
        job = test_ci["jobs"][job_name]
        assert_that(job["with"]["test-path"]).described_as(job_name).is_equal_to(
            "tests",
        )
        # Assert the absence of *any* ignore, not just this path spelling:
        # "--ignore=tests" or "--ignore tests/integration" would exclude the
        # suite again while still passing a substring check for the path.
        assert_that(job["with"]["extra-args"]).described_as(
            job_name,
        ).does_not_contain("--ignore", "tests/integration")


def test_test_ci_suite_coverage_gate_mirrors_test_gate() -> None:
    """Required coverage gate mirrors test-gate with no pipeline=false shortcut.

    The Python matrix always runs (#2108), so a path-skip success shortcut
    would false-green this required check while tests actually failed.
    """
    test_ci = _load_workflow(name="test-ci.yml")
    gate = test_ci["jobs"]["test-suite-coverage"]

    assert_that(gate["needs"]).is_equal_to(["test-gate"])
    assert_that(gate["with"]).does_not_contain_key("pipeline-skip")
    assert_that(gate["with"]["upstream-result"]).is_equal_to(
        "${{ needs.test-gate.outputs.result }}",
    )
    assert_that(gate["with"]["passed-output"]).is_equal_to(
        "${{ needs.test-gate.outputs.passed }}",
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
        ".cursor",
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
        ".spectral.yaml",
        ".stylelintrc.json",
        ".typos.toml",
        ".vale.ini",
        ".yamllint",
        "apps",
        "benchmarks",
        "bun.lock",
        "commitlint.config.js",
        "docker",
        "docker-compose.yml",
        "Dockerfile",
        "evals",  # offline review-efficacy harness (#2147): linted Python
        "justfile",
        "LICENSE",
        "lintro",
        "lintro_build",
        "MANIFEST.in",
        "npm",
        "package.json",
        "pyproject.toml",
        "renovate.json",
        "requirements-semgrep.in",
        "requirements-semgrep.txt",
        "scripts",
        "socket.yml",
        "test_samples",
        "tests",
        "tools",
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


def test_publish_npm_refuses_untrusted_entry_before_the_npm_environment() -> None:
    """A run that cannot authenticate fails before the npm approval is spent.

    npm trusted publishing only authenticates the tag-pipeline entry path
    (issue #2247), so a live direct dispatch can never publish. The guard must
    run in its own job that carries no ``environment:`` and that the
    environment-gated publish job ``needs``, otherwise the doomed run burns an
    ``npm`` deployment approval before failing.
    """
    workflow = _load_workflow(name="publish-npm.yml")
    jobs = workflow["jobs"]

    guard = jobs["guard"]
    assert_that(guard).does_not_contain_key("environment")

    publish_needs = jobs["publish"]["needs"]
    if isinstance(publish_needs, str):
        publish_needs = [publish_needs]
    assert_that(publish_needs).contains("guard")
    assert_that(jobs["publish"]["environment"]).is_equal_to("npm")

    guard_step = next(
        (
            step
            for step in guard["steps"]
            if step.get("run", "").strip().endswith("assert_dispatch_allowed.sh")
        ),
        None,
    )
    assert_that(guard_step).described_as("guard step not found").is_not_none()
    assert guard_step is not None  # narrow type for mypy
    # The decision logic lives in the script, not inline in the workflow.
    assert_that(guard_step["run"].strip()).is_equal_to(
        "scripts/ci/npm/assert_dispatch_allowed.sh",
    )
    # Both inputs the guard decides on must reach the script: the entry
    # workflow (the OIDC subject) and dry_run.
    assert_that(guard_step["env"]["WORKFLOW_REF"]).contains("github.workflow_ref")
    assert_that(guard_step["env"]["DRY_RUN"]).contains("inputs.dry_run")


def _guard_allowlisted_workflow() -> str:
    """Return the workflow filename the npm guard allowlists.

    Returns:
        The basename of the entry workflow named in
        ``TRUSTED_ENTRY_WORKFLOW`` inside ``assert_dispatch_allowed.sh``.
    """
    script = (
        _REPO_ROOT / "scripts" / "ci" / "npm" / "assert_dispatch_allowed.sh"
    ).read_text(encoding="utf-8")
    match = re.search(
        r"^readonly TRUSTED_ENTRY_WORKFLOW='/\.github/workflows/([^']+)@'",
        script,
        flags=re.MULTILINE,
    )
    assert_that(match).described_as("TRUSTED_ENTRY_WORKFLOW not found").is_not_none()
    assert match is not None  # narrow type for mypy
    return match.group(1)


def test_publish_npm_guard_allowlists_a_workflow_that_calls_it() -> None:
    """The allowlisted entry workflow exists and really calls publish-npm.yml.

    The guard is an allowlist keyed on a workflow *filename*, so a rename on
    either side would silently lock out every publish (or, with a denylist,
    let an unauthenticable one through). Pin both halves: the named workflow
    is on disk, and it is the one that invokes publish-npm.yml.
    """
    allowlisted = _guard_allowlisted_workflow()
    entry_path = _REPO_ROOT / ".github" / "workflows" / allowlisted
    assert_that(entry_path.is_file()).described_as(str(entry_path)).is_true()

    entry_workflow = _load_workflow(name=allowlisted)
    callers = [
        job
        for job in entry_workflow["jobs"].values()
        if isinstance(job, dict)
        and str(job.get("uses", "")).endswith("publish-npm.yml")
    ]
    assert_that(callers).described_as(
        f"{allowlisted} must call publish-npm.yml",
    ).is_not_empty()


def test_publish_npm_guard_script_allowlists_the_trusted_entry_workflow() -> None:
    """Only the tag pipeline may run a live publish; everything else fails.

    ``github.event_name`` cannot substitute for the entry workflow: a
    ``workflow_call`` run reports the *caller's* event, so a dispatched
    tag-pipeline run and a dispatched publish-npm.yml run look identical. And
    the check is an allowlist, so an unknown or renamed caller is refused
    rather than waved through. The runner's own ``GITHUB_WORKFLOW_REF`` is the
    fallback, so a dropped ``env:`` mapping still gates the publish.
    """
    script = _REPO_ROOT / "scripts" / "ci" / "npm" / "assert_dispatch_allowed.sh"
    workflows = "lgtm-hq/py-lintro/.github/workflows"
    trusted = _guard_allowlisted_workflow()
    tag_pipeline_ref = f"{workflows}/{trusted}@refs/tags/v1.2.3"
    dispatch_ref = f"{workflows}/publish-npm.yml@refs/heads/main"
    unset = "<unset>"
    cases: list[tuple[dict[str, str], int]] = [
        # The trusted entry workflow, on any ref: allowed.
        ({"WORKFLOW_REF": tag_pipeline_ref}, 0),
        # An absent or empty DRY_RUN is a live publish, not a dry run: a
        # dispatch must still be refused, or a dropped input would open the gate.
        ({"WORKFLOW_REF": dispatch_ref, "DRY_RUN": unset}, 1),
        ({"WORKFLOW_REF": dispatch_ref, "DRY_RUN": ""}, 1),
        ({"WORKFLOW_REF": f"{workflows}/{trusted}@refs/heads/main"}, 0),
        # Direct dispatch of this workflow: refused unless it is a dry run.
        ({"WORKFLOW_REF": dispatch_ref}, 1),
        ({"WORKFLOW_REF": dispatch_ref, "DRY_RUN": "true"}, 0),
        # An unknown caller is not on the allowlist.
        ({"WORKFLOW_REF": f"{workflows}/some-other-pipeline.yml@refs/tags/v1"}, 1),
        # With no WORKFLOW_REF mapping, the runner's own GITHUB_WORKFLOW_REF
        # still gates: a dropped `env:` in the workflow must not open the gate.
        ({"GITHUB_WORKFLOW_REF": tag_pipeline_ref}, 0),
        ({"GITHUB_WORKFLOW_REF": dispatch_ref}, 1),
        # No entry path at all proves nothing: fail closed.
        ({}, 1),
    ]
    for env, expected_code in cases:
        merged = {"PATH": "/usr/bin:/bin", "DRY_RUN": "false", **env}
        merged = {key: value for key, value in merged.items() if value != unset}
        result = subprocess.run(  # nosec B603 - fixed in-repo script
            [str(script)],
            env=merged,
            capture_output=True,
            text=True,
            check=False,
        )
        assert_that(result.returncode).described_as(str(env)).is_equal_to(
            expected_code,
        )


def test_publish_npm_classifies_e404_as_non_retryable() -> None:
    """publish_packages.sh classifies npm's masked-auth E404 as fatal.

    npm reports an unauthorized publish as ``E404 Not Found`` (issue #2247).
    Retrying it burns three attempts per package on a permanent condition, so
    E404 belongs in the non-retryable class, not the transient one. This is a
    wiring assertion on the two classification patterns; the behaviour (one
    attempt, no retry) is covered by
    ``tests/bats/unit/npm/test_publish_packages_e404.bats``.
    """
    script = (_REPO_ROOT / "scripts" / "ci" / "npm" / "publish_packages.sh").read_text(
        encoding="utf-8",
    )
    non_retryable = re.search(
        r"^NON_RETRYABLE_ERROR_RE='([^']*)'",
        script,
        flags=re.MULTILINE,
    )
    transient = re.search(
        r"^TRANSIENT_ERROR_RE='([^']*)'",
        script,
        flags=re.MULTILINE,
    )
    assert_that(non_retryable).is_not_none()
    assert_that(transient).is_not_none()
    assert non_retryable is not None and transient is not None  # narrow for mypy
    assert_that(non_retryable.group(1).split("|")).contains("E404")
    assert_that(transient.group(1)).does_not_contain("E404")


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
    workflow = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    pinned = workflow["env"]["UV_VERSION"]
    assert_that(pinned).matches(r"^\d+\.\d+\.\d+$")
    assert_that(pinned).described_as(
        "build-binaries UV_VERSION must match docker/tools.Dockerfile ARG UV_VERSION",
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


# #2562: the binary workflows are workflow_call only. The former
# build-binary.yml carried a workflow_dispatch repair path whose get-release-info
# resolved the *latest published* release and republished the dispatched ref's
# binaries onto it (#2484). Every publishing step was gated on
# ``inputs.release_tag != '' || inputs.upload_to_release == true`` to keep a
# bare dispatch side-effect free; with the trigger gone the gate and its
# evaluator are gone too. Recovery is lgtm-hq/lgtm-ci#966.

_BINARY_WORKFLOWS = (_BUILD_BINARY_WORKFLOW, _PUBLISH_BINARIES_WORKFLOW)


@pytest.mark.parametrize("workflow_name", _BINARY_WORKFLOWS)
def test_binary_workflows_are_workflow_call_only(workflow_name: str) -> None:
    """Neither binary stage can be dispatched by hand (#2562, #2484).

    A dispatch of the old workflow resolved the latest published release and
    could republish main-HEAD binaries onto it. Both halves of the split take
    the tag from their caller and nowhere else: ``workflow_call`` is the only
    trigger, ``release_tag`` is required, and the ``upload_to_release`` repair
    input no longer exists anywhere.

    Args:
        workflow_name: The binary workflow under test.
    """
    workflow = _load_workflow(name=workflow_name)
    triggers = workflow["on"]
    assert_that(set(triggers)).described_as(workflow_name).is_equal_to(
        {"workflow_call"},
    )
    release_tag = triggers["workflow_call"]["inputs"]["release_tag"]
    assert_that(release_tag["required"]).described_as(workflow_name).is_true()
    assert_that(release_tag["type"]).described_as(workflow_name).is_equal_to("string")
    assert_that(triggers["workflow_call"]["inputs"]).does_not_contain_key(
        "upload_to_release",
    )
    text = (_REPO_ROOT / ".github" / "workflows" / workflow_name).read_text(
        encoding="utf-8",
    )
    assert_that(text).described_as(workflow_name).does_not_contain("upload_to_release")
    assert_that(text).described_as(workflow_name).does_not_contain("workflow_dispatch")


def test_readme_no_longer_documents_the_binary_repair_dispatch() -> None:
    """The workflows README describes the two-stage split, not a dispatch.

    The "Dispatching build-binary.yml by hand" runbook told an operator how
    to republish onto the latest release; with the trigger gone that text
    would send them to a form that no longer exists (#2562). The README has
    to name both stages, the recovery pointer, and the rerun path instead.
    """
    readme = (_REPO_ROOT / ".github" / "workflows" / "README.md").read_text(
        encoding="utf-8",
    )
    for stale in ("upload_to_release", "### Dispatching", "Repair dispatch"):
        assert_that(readme).described_as(stale).does_not_contain(stale)
    for expected in (
        _BUILD_BINARY_WORKFLOW,
        _PUBLISH_BINARIES_WORKFLOW,
        "lgtm-hq/lgtm-ci#966",
        "Re-run failed jobs",
        "attest-build-provenance",
        "90 days",
    ):
        assert_that(readme).described_as(expected).contains(expected)


def test_renovate_manages_build_binary_uv_pin() -> None:
    """A Renovate customManager must match the build-binary UV_VERSION line.

    The workflow pin is not a native manager target, so without a regex
    manager whose pattern actually matches the file's text it would silently
    rot while the Dockerfile pin advanced (#1487).
    """
    config = json.loads((_REPO_ROOT / "renovate.json").read_text(encoding="utf-8"))
    workflow_text = (
        _REPO_ROOT / ".github" / "workflows" / _BUILD_BINARY_WORKFLOW
    ).read_text(encoding="utf-8")

    matching = [
        manager
        for manager in config["customManagers"]
        if any(
            _BUILD_BINARY_WORKFLOW in pattern
            for pattern in manager.get("managerFilePatterns", [])
        )
    ]
    assert_that(matching).described_as(
        "no Renovate customManager targets build-binaries.yml",
    ).is_not_empty()

    # The docker-ci semgrep-lock job carries the same pin (#2436); the same
    # manager must cover it or the second site would rot.
    uv_managers = [
        manager
        for manager in matching
        if any("UV_VERSION" in pattern for pattern in manager.get("matchStrings", []))
    ]
    assert_that(uv_managers).is_not_empty()
    assert_that(
        [
            pattern
            for manager in uv_managers
            for pattern in manager["managerFilePatterns"]
        ],
    ).contains(".github/workflows/docker-ci.yml")

    for manager in matching:
        assert_that(manager["packageNameTemplate"]).is_equal_to("astral-sh/uv")
        for match_string in manager["matchStrings"]:
            # Renovate uses JS named groups, `(?<name>...)`; Python wants
            # `(?P<name>...)`.
            pattern = re.sub(r"\(\?<(\w+)>", r"(?P<\1>", match_string)
            found = re.search(pattern, workflow_text)
            assert_that(found).described_as(
                f"matchString {match_string!r} does not match build-binaries.yml",
            ).is_not_none()


def test_renovate_does_not_automerge_golangci_lint_pin() -> None:
    """golangci-lint pin bumps must not automerge independently of the tools digest.

    The regex manager writes only ``ToolName.GOLANGCI_LINT`` in
    ``_tool_versions.py``. The app image copies binaries from the
    digest-pinned ``lintro-tools`` image, so a versions-only merge fails
    Docker verify (#2139, #2220). Automerge stays off so the pin and the
    matching digest can land together.
    """
    config = json.loads((_REPO_ROOT / "renovate.json").read_text(encoding="utf-8"))
    managers = [
        manager
        for manager in config["customManagers"]
        if manager.get("packageNameTemplate") == "golangci/golangci-lint"
    ]
    assert_that(managers).is_not_empty()

    rules = [
        rule
        for rule in config.get("packageRules", [])
        if "golangci/golangci-lint" in (rule.get("matchPackageNames") or [])
        and rule.get("automerge") is False
    ]
    assert_that(rules).described_as(
        "no packageRule disables automerge for golangci/golangci-lint",
    ).is_not_empty()


def test_build_binary_retries_setup_uv_on_failure() -> None:
    """Each setup-uv job keeps a continue-on-error + retry pair (#1513)."""
    workflow = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
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


def _uv_commands(script: str) -> list[list[str]]:
    """Extract the ``uv`` invocations from a workflow ``run:`` script.

    Backslash continuations are folded first so a multi-line invocation is seen
    as one command, and each command is tokenised with ``shlex`` so quoted
    values (``--group "dev"``) parse the same as bare ones. Leading
    ``NAME=value`` environment prefixes are stripped before matching.

    Args:
        script: The raw text of a step's ``run:`` block.

    Returns:
        One token list per ``uv sync`` / ``uv run`` invocation found.
    """
    folded = re.sub(r"\\\s*\n", " ", script)
    commands: list[list[str]] = []
    for segment in re.split(r"[\n;]|\|\||&&|(?<!\|)\|(?!\|)", folded):
        try:
            tokens = shlex.split(segment, comments=True)
        except ValueError:  # unbalanced quotes in a non-command fragment
            continue
        while tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0]):
            tokens = tokens[1:]
        if tokens[:2] in (["uv", "sync"], ["uv", "run"]):
            commands.append(tokens)
    return commands


def test_binary_jobs_never_install_the_dev_group() -> None:
    """Binary builds must opt out of the default dependency groups (#1897).

    The default ``dev`` group pulls ``mcp`` -> ``pyjwt[crypto]`` ->
    ``cryptography``, which ships no macOS x86_64 wheel from 49.0.0 on. Syncing
    it makes uv build that package from source via maturin/cargo, which then
    dies on the (deliberately) blocked crates.io egress. Every ``uv`` command in
    the binary jobs therefore has to carry ``--no-default-groups`` -- including
    ``uv run``, which otherwise re-syncs the default groups back in.
    """
    workflow = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    binary_jobs = ("build-macos", "build-linux")

    for job_id in binary_jobs:
        steps = workflow["jobs"][job_id]["steps"]
        uv_commands = [
            command
            for step in steps
            for command in _uv_commands(script=step.get("run") or "")
        ]
        assert_that(uv_commands).described_as(f"{job_id} uv commands").is_not_empty()

        for tokens in uv_commands:
            rendered = " ".join(tokens)
            groups = [
                value
                for flag, value in zip(tokens, tokens[1:], strict=False)
                if flag == "--group"
            ]
            assert_that(groups).described_as(
                f"{job_id}: {rendered!r} must not sync the dev group",
            ).does_not_contain("dev")
            assert_that(tokens).described_as(
                f"{job_id}: {rendered!r} must pass --no-default-groups",
            ).contains("--no-default-groups")


def test_build_linux_allows_the_hosted_runner_watchdog() -> None:
    """The Linux binary build must allow GitHub's hosted-runner watchdog.

    harden-runner block mode denied ``hosted-compute-watchdog-*.githubapp.com``
    and ``hosted-compute-request-orchestrator-*.githubapp.com``, and the x64
    build was reclaimed mid-run on every attempt for v0.147.7 with "The runner
    has received a shutdown signal" (#1761, #2339). The arm64 sibling runs the
    same source on the same runner class and has never been observed dying that
    way, which leaves the enforced egress allowlist as the lead.
    """
    workflow = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    job = workflow["jobs"]["build-linux"]
    harden = next(
        step
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("step-security/harden-runner@")
    )
    endpoints = str(harden["with"]["allowed-endpoints"]).split()

    assert_that(harden["with"]["egress-policy"]).is_equal_to("block")
    # Exact hosts by owner decision (#2339): every hosted-compute shard
    # observed in this repo's job logs, for both control-plane families, plus
    # the results receiver. The agreed fallback if a new shard appears is a
    # revert to the `*.githubapp.com:443` wildcard in a follow-up PR, so this
    # test pins the literals but does not forbid that wildcard.
    for family in ("hosted-compute-watchdog", "hosted-compute-request-orchestrator"):
        for shard in ("iad-01", "iad-02", "eus-01", "eus-02"):
            assert_that(endpoints).contains(
                f"{family}-prod-{shard}.githubapp.com:443",
            )
    assert_that(endpoints).contains(
        "actions-results-receiver-production.githubapp.com:443",
    )
    # The job must still carry its baseline: build-binaries.yml is read from
    # the tag, so a shrunk list passes every PR and fails at the release.
    assert_that(endpoints).contains(
        "pypi.org:443",
        "files.pythonhosted.org:443",
        "nuitka.net:443",
        "release-assets.githubusercontent.com:443",
    )
    assert_that(endpoints).does_not_contain_duplicates()
    # Any other glob would silently widen the block policy; the agreed
    # revert-to-wildcard fallback is the single form permitted here.
    for endpoint in endpoints:
        if "*" in endpoint:
            assert_that(endpoint).described_as(
                f"{endpoint}: only the agreed *.githubapp.com:443 fallback may glob",
            ).is_equal_to("*.githubapp.com:443")


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


def test_auto_rerun_allows_three_reruns() -> None:
    """Persistent runner-loss failures may receive up to three reruns (#2237)."""
    workflow = _load_workflow(name="auto-rerun-on-infra-failure.yml")
    rerun_inputs = workflow["jobs"]["rerun"]["with"]

    assert_that(rerun_inputs["max-reruns"]).is_equal_to("3")


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


_SIGSTORE_HOSTS = frozenset(
    {
        "fulcio.sigstore.dev:443",
        "rekor.sigstore.dev:443",
        "tuf-repo-cdn.sigstore.dev:443",
        "timestamp.sigstore.dev:443",
        "oauth2.sigstore.dev:443",
    },
)
_OIDC_HOST = "token.actions.githubusercontent.com:443"


def _endpoint_set(value: object) -> set[str] | None:
    """Split a literal ``allowed-endpoints`` block into a host set.

    Args:
        value: The raw ``allowed-endpoints`` value from the workflow.

    Returns:
        The host set (empty when the block is missing, which under ``replace``
        semantics blocks all egress), or ``None`` when the value is an
        expression that a resolver job fills in at run time (covered by its
        own test).
    """
    if isinstance(value, str) and "${{" in value:
        return None
    if not isinstance(value, str):
        return set()
    return set(value.split())


def _attesting_jobs() -> list[tuple[str, str, set[str] | None, bool]]:
    """Collect every job that signs an attestation and its literal allowlist.

    A job attests when it runs ``actions/attest-build-provenance`` or
    ``sigstore/cosign-installer`` directly, calls lgtm-ci's
    ``reusable-build-python-dist.yml`` (which attests dist/* since v0.70.0),
    or calls a ``reusable-docker*`` workflow with ``cosign-sign: true``. For a
    reusable call the caller's list only matters under ``replace`` semantics.

    Returns:
        Tuples of (workflow file, job name, allowlist or None, needs OIDC).
    """
    found: list[tuple[str, str, set[str] | None, bool]] = []
    for path in _workflow_paths():
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_name, job in (workflow.get("jobs") or {}).items():
            uses = str(job.get("uses", ""))
            with_block = job.get("with") or {}
            if "reusable-build-python-dist.yml" in uses:
                if with_block.get("allowed-endpoints-mode") == "replace":
                    found.append(
                        (
                            path.name,
                            job_name,
                            _endpoint_set(with_block.get("allowed-endpoints")),
                            True,
                        ),
                    )
                continue
            if "reusable-docker" in uses and with_block.get("cosign-sign") is True:
                if with_block.get("allowed-endpoints-mode") == "replace":
                    found.append(
                        (
                            path.name,
                            job_name,
                            _endpoint_set(with_block.get("allowed-endpoints")),
                            False,
                        ),
                    )
                continue
            steps = job.get("steps") or []
            step_uses = [str(step.get("uses", "")) for step in steps]
            attests = any(
                u.startswith("actions/attest-build-provenance@") for u in step_uses
            )
            signs = any(u.startswith("sigstore/cosign-installer@") for u in step_uses)
            if not (attests or signs):
                continue
            harden = [
                step
                for step in steps
                if str(step.get("uses", "")).startswith("step-security/harden-runner@")
            ]
            assert_that(harden).described_as(
                f"{path.name}:{job_name} attests but has no harden-runner step",
            ).is_length(1)
            found.append(
                (
                    path.name,
                    job_name,
                    _endpoint_set(
                        (harden[0].get("with") or {}).get("allowed-endpoints"),
                    ),
                    attests,
                ),
            )
    return found


def test_every_attesting_job_allows_the_sigstore_hosts() -> None:
    """Every job that signs an attestation must let Sigstore traffic out.

    Under ``allowed-endpoints-mode: replace`` the caller's list is passed
    verbatim to harden-runner, so a list copied from the pypi preset silently
    drops the Sigstore hosts the reusable's own default carried. The
    v0.160.3a3 checkpoint died that way: the dist attest step got
    ``ECONNREFUSED`` from fulcio.sigstore.dev after a green build (#2562).
    Jobs that mint an OIDC token for the attestation also need the token
    endpoint. A missing allowlist counts as empty, not as exempt.
    """
    jobs = _attesting_jobs()
    names = {(workflow, job) for workflow, job, _, _ in jobs}
    assert_that(names).contains(
        ("publish-pypi-on-tag.yml", "pypi-build"),
        ("publish-pypi-on-tag.yml", "docker-promote"),
    )
    assert_that([job for job in names if job[0] == "build-binaries.yml"]).is_not_empty()

    offenders: list[str] = []
    for workflow, job, endpoints, needs_oidc in jobs:
        if endpoints is None:
            continue
        required = set(_SIGSTORE_HOSTS)
        if needs_oidc:
            required.add(_OIDC_HOST)
        missing = sorted(required - endpoints)
        if missing:
            offenders.append(f"{workflow}:{job} missing {missing}")
    assert_that(offenders).described_as(
        "attesting jobs whose replace-mode allowlist blocks Sigstore",
    ).is_empty()


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
                        f"{path.name}:{job_id}: checkout ref {with_block.get('ref')!r}",
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
    assert_that(allowed).contains("release-assets.githubusercontent.com:443")
    assert_that(allowed).contains("github-releases.githubusercontent.com:443")


@pytest.mark.parametrize("job_id", ["test-compat", "test-coverage"])
def test_test_jobs_omit_pypi_publish_endpoints(job_id: str) -> None:
    """Test jobs must not allowlist PyPI publish/alt-index hosts (#1351).

    ``pytest`` only downloads from real PyPI. ``allowed-endpoints-mode:
    replace`` is load-bearing so ``egress-preset: pypi`` cannot merge
    publish hosts back in. ``upload.test.pypi.org`` was never in this
    caller list; the omit assertion keeps the preset from reintroducing it.

    Args:
        job_id: Test job whose egress allowlist is under test.
    """
    workflow = _load_workflow(name="test-ci.yml")
    job_with = workflow["jobs"][job_id]["with"]
    allowed = set(job_with["allowed-endpoints"].split())

    assert_that(job_with["allowed-endpoints-mode"]).is_equal_to("replace")
    assert_that(allowed).contains("pypi.org:443")
    assert_that(allowed).contains("files.pythonhosted.org:443")
    assert_that(allowed).does_not_contain("test.pypi.org:443")
    assert_that(allowed).does_not_contain("upload.pypi.org:443")
    assert_that(allowed).does_not_contain("upload.test.pypi.org:443")


@pytest.mark.parametrize("job_id", ["test-compat", "test-coverage"])
def test_test_jobs_allow_setup_uv_github_release_fallback(job_id: str) -> None:
    """Test jobs must allow setup-uv fallback while the Astral mirror lags.

    The explicit allowlist uses replace semantics. When a newly published uv
    version is not yet mirrored at releases.astral.sh, setup-uv falls back to
    GitHub Releases and follows redirects to the release-asset hosts.

    Args:
        job_id: Test job whose egress allowlist is under test.
    """
    workflow = _load_workflow(name="test-ci.yml")
    allowed = set(workflow["jobs"][job_id]["with"]["allowed-endpoints"].split())

    assert_that(allowed).contains(
        "release-assets.githubusercontent.com:443",
        "github-releases.githubusercontent.com:443",
    )


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


def _regenerate_step_index(steps: list[dict[str, object]]) -> int:
    """Return the index of the version-artifact regeneration step.

    Args:
        steps: A workflow job's step list.

    Returns:
        Index of the step running ``generate-tool-versions.py``.
    """
    for index, step in enumerate(steps):
        if "generate-tool-versions.py" in str(step.get("run", "")):
            return index
    pytest.fail("no generate-tool-versions.py step found")


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


# --- Nightly image resolution (#2602) ---------------------------------------
#
# The nightly used to lint with a hand-maintained
# ``py-lintro:<version>@sha256:...`` pin repeated at five sites. Nothing moved
# it, so it froze at 0.148.0 while main reached 0.156.x, and the digest-lag
# gate failed every single night on mismatches that were pure pin lag — a
# permanently red nightly that hid real regressions. The image is resolved at
# run time now, and these tests are what stop a literal creeping back.

# Every place the nightly names the image it lints with.
_NIGHTLY_IMAGE_CONSUMERS = ("dogfood-full", "dogfood_full_retry")
_NIGHTLY_IMAGE_STEP_CONSUMERS = ("dogfood-skip-gate", "dogfood_skip_gate_retry")


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


def _resolve_prune_min_age_days(expression: str, *, dispatch_input: str) -> int:
    """Resolve the prune ``min-age-days`` expression for a dispatch input.

    Mirrors GitHub expression semantics for the ``fromJSON(A || B)`` form:
    ``||`` yields the first operand whose string value is non-empty, and
    ``fromJSON`` parses the chosen string as JSON. Substitution reduces the
    expression to string literals only; the operands are matched literally,
    never ``eval()``-ed.
    """
    inner = _normalize_github_expr(expression)
    assert inner.startswith("${{") and inner.endswith("}}")
    inner = inner[3:-2].strip()
    match = re.fullmatch(r"fromJSON\((.+?)\s*\|\|\s*(.+?)\)", inner)
    assert match is not None, f"unexpected min-age-days form: {inner}"
    left, right = (operand.strip() for operand in match.groups())
    values = {"inputs.min_age_days": dispatch_input}
    left_value = values.get(left, left.strip("'\""))
    right_value = values.get(right, right.strip("'\""))
    chosen = left_value or right_value
    parsed = json.loads(chosen)
    assert isinstance(parsed, int), f"min-age-days is not a number: {chosen!r}"
    return parsed


@pytest.mark.parametrize(
    ("dispatch_input", "expected"),
    [
        ("", 7),  # scheduled path: the dispatch input is empty -> '7' default
        ("7", 7),  # dispatched default arrives as the string '7'
        ("14", 14),  # dispatched override coerces to a number
    ],
)
def test_ghcr_cleanup_prune_min_age_resolves_to_a_number(
    dispatch_input: str,
    expected: int,
) -> None:
    """The prune min-age resolves to a number on every trigger path (#2603).

    A number-typed ``workflow_dispatch`` input arrives as a string and the
    scheduled path leaves the input empty; either passed straight through,
    the reusable call is rejected at plan time ("Unexpected value '7'",
    runs 34766253077 and 34766393903). The ``|| '7'`` default makes the
    empty scheduled case resolve to 7 days.
    """
    cleanup = _load_workflow(name="ghcr-cleanup.yml")
    for job_name in ("prune-untagged", "prune-untagged-base"):
        expression = str(cleanup["jobs"][job_name]["with"]["min-age-days"])
        assert_that(
            _resolve_prune_min_age_days(
                expression,
                dispatch_input=dispatch_input,
            ),
        ).is_equal_to(expected)


def test_ghcr_cleanup_prune_legs_forward_a_number_timeout() -> None:
    """The caller raises the reusable's prune job timeout to 30 (#2603).

    The reusable defaults ``timeout-minutes`` to 10, and its default cancelled
    the ``py-lintro`` untagged prune leg mid-enumeration (run 34774902409) —
    the leg never reported, so the sweep silently lost a package. Both prune
    callers must forward a bare number literal (the plan-time string gotcha
    from the min-age fix applies here too), not a block scalar or string.
    """
    cleanup = _load_workflow(name="ghcr-cleanup.yml")
    for job_name in ("prune-untagged", "prune-untagged-base"):
        timeout = cleanup["jobs"][job_name]["with"]["timeout-minutes"]
        assert_that(timeout).described_as(job_name).is_instance_of(int)
        assert_that(timeout).described_as(job_name).is_equal_to(30)


def test_publish_pypi_top_level_permissions_are_empty() -> None:
    """The tag publisher grants no scopes at the top level (#2511).

    Every job in ``publish-pypi-on-tag.yml`` declares its own ``permissions``
    block, so a top-level grant is dead configuration that only widens the
    default token. The ``actions: read`` the binary build stage needs belongs
    on the ``build-binaries`` caller job (#2440, #2562); the tap dispatch is
    read-only since the release reusable attaches the assets.
    """
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    assert_that(publish["permissions"]).is_equal_to({})
    homebrew = publish["jobs"]["homebrew-tap"]["permissions"]
    assert_that(homebrew).is_equal_to({"contents": "read"})
    build = publish["jobs"]["build-binaries"]["permissions"]
    assert_that(build).contains_entry({"actions": "read"})
    assert_that(build).contains_entry({"contents": "read"})


def test_no_testpypi_workflow_or_endpoints_remain() -> None:
    """The dead TestPyPI staging lane stays deleted (#2601).

    ``publish-testpypi.yml`` never ran once, so it was a secret and an
    environment kept alive for nothing. Releases are verified by installing
    from real PyPI. The egress assertion is what stops the lane creeping back
    in as an allowlisted upload host on the production build job.
    """
    workflow_dir = _REPO_ROOT / ".github" / "workflows"
    matches = sorted(path.name for path in workflow_dir.glob("*testpypi*"))
    assert_that(matches).is_empty()

    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    build_with = publish["jobs"]["pypi-build"]["with"]
    allowed = set(build_with["allowed-endpoints"].split())
    assert_that(build_with["allowed-endpoints-mode"]).is_equal_to("replace")
    assert_that(allowed).does_not_contain("test.pypi.org:443")
    assert_that(allowed).does_not_contain("upload.test.pypi.org:443")

    # The upload job carries its own harden-runner allowlist; a staging host
    # must not creep back in there either.
    harden = next(
        step
        for step in publish["jobs"]["pypi-upload"]["steps"]
        if "harden-runner" in str(step.get("uses", ""))
    )
    upload_allowed = set(str(harden["with"]["allowed-endpoints"]).split())
    assert_that(upload_allowed).does_not_contain("test.pypi.org:443")
    assert_that(upload_allowed).does_not_contain("upload.test.pypi.org:443")


def test_publish_pypi_sbom_fails_on_high_severity() -> None:
    """Release SBOM must gate publishes on high/critical vulns (#1118)."""
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    sbom = publish["jobs"]["sbom"]
    assert_that(sbom["with"]).contains_entry({"fail-on-severity": "high"})
    assert_that(sbom["with"].get("scan-vulnerabilities")).is_true()


@pytest.mark.parametrize(
    "workflow_name",
    ["publish-pypi-on-tag.yml", "sbom-on-main.yml"],
)
def test_sbom_callers_grant_contents_read_only(workflow_name: str) -> None:
    """SBOM callers stay read-only on contents (#1807).

    Generation, vulnerability scan and attestation only read the repository.
    The ``contents: write`` release-asset upload moved into
    ``reusable-sbom-release-upload.yml`` upstream (lgtm-ci#770), so a
    ``contents: write`` grant here is silent over-permissioning on the
    production tag-publish path.
    """
    permissions = _load_workflow(name=workflow_name)["jobs"]["sbom"]["permissions"]
    assert_that(permissions).contains_entry({"contents": "read"})
    # Attestation signing and code-scanning upload still need these.
    assert_that(permissions).contains_entry({"security-events": "write"})
    assert_that(permissions).contains_entry({"id-token": "write"})
    assert_that(permissions).contains_entry({"attestations": "write"})


# --- Mirror release automation (#1171) ---------------------------------------

_SHA_PIN_RE = re.compile(r"@[0-9a-f]{40}$")


def _job_steps(workflow: dict[str, Any], *, job: str) -> list[dict[str, Any]]:
    """Return the ordered steps of a workflow job.

    Args:
        workflow: Parsed workflow document.
        job: Job key.

    Returns:
        The job's ``steps`` list.
    """
    return cast(list[dict[str, Any]], workflow["jobs"][job]["steps"])


def test_mirror_release_serializes_with_global_concurrency() -> None:
    """Only one mirror bump runs at a time to avoid clobbering newer pins."""
    workflow = _load_workflow(name="mirror-release.yml")

    assert_that(workflow["concurrency"]["group"]).is_equal_to("mirror-release")
    assert_that(workflow["concurrency"]["cancel-in-progress"]).is_false()


def test_mirror_release_job_has_timeout() -> None:
    """Mirror bump inherits a bounded job timeout instead of the 6-hour default."""
    workflow = _load_workflow(name="mirror-release.yml")

    timeout = workflow["jobs"]["mirror-bump"]["timeout-minutes"]
    assert_that(timeout).is_instance_of(int)
    assert_that(timeout).is_equal_to(20)


def test_mirror_release_is_called_not_release_triggered() -> None:
    """Mirror bump is a reusable call plus a manual dispatch fallback.

    A ``release: published`` trigger is unreachable here: the tag pipeline
    creates the release with GITHUB_TOKEN and GitHub suppresses workflow
    events for actions taken with that token, so the workflow logged zero runs across
    every release since (#2599). ``push: tags`` carries the same recursion
    guard, so ``workflow_call`` is the only trigger the automated train fires.
    """
    workflow = _load_workflow(name="mirror-release.yml")
    triggers = workflow["on"]

    assert_that(triggers).contains_key("workflow_call", "workflow_dispatch")
    assert_that(triggers).does_not_contain_key("release")
    assert_that(triggers).does_not_contain_key("push")
    assert_that(triggers["workflow_call"]["inputs"]).contains_key("release_tag")
    assert_that(
        triggers["workflow_call"]["inputs"]["release_tag"]["required"],
    ).is_true()
    assert_that(triggers["workflow_call"]["secrets"]).contains_key(
        "MIRROR_REPO_TOKEN",
    )
    assert_that(triggers["workflow_dispatch"]["inputs"]).contains_key("release_tag")
    assert_that(workflow["jobs"]["mirror-bump"]["env"]["RELEASE_TAG"]).is_equal_to(
        "${{ inputs.release_tag }}",
    )


def test_tag_pipeline_calls_the_mirror_after_the_github_release() -> None:
    """The tag pipeline is what fires the mirror bump, after the release job.

    Pins the whole repair from #2599: a caller job exists, it waits for the
    release the mirror mirrors, it passes the pushed tag, it hands over the
    cross-repo token, and it grants at least what the callee's job requests
    (a shortfall is a logless ``startup_failure``; see #2484/#2563).
    """
    caller = _load_workflow(name="publish-pypi-on-tag.yml")
    callee = _load_workflow(name="mirror-release.yml")
    job = caller["jobs"]["mirror-release"]

    assert_that(job["uses"]).is_equal_to("./.github/workflows/mirror-release.yml")
    assert_that(job["needs"]).contains("github-release")
    assert_that(job["with"]["release_tag"]).is_equal_to("${{ github.ref_name }}")
    assert_that(job["secrets"]["MIRROR_REPO_TOKEN"]).is_equal_to(
        "${{ secrets.MIRROR_REPO_TOKEN }}",
    )
    # `secrets: inherit` would hand the call every org/repo secret.
    assert_that(job["secrets"]).is_instance_of(dict)

    granted = _effective_grant(job=job, workflow=caller)
    for callee_job in callee["jobs"].values():
        requested = _effective_grant(job=callee_job, workflow=callee)
        for scope, level in requested.items():
            assert_that(_granted_level(granted, scope=scope)).described_as(
                f"caller grant for {scope}",
            ).is_greater_than_or_equal_to(level)


def test_mirror_token_guard_probes_the_secret_into_an_output() -> None:
    """A guard job turns the unreadable secret into a job output (#2622).

    Secrets cannot be referenced from a job-level ``if``, so the only way to
    gate the mirror call on ``MIRROR_REPO_TOKEN`` existing is to read it into
    a step env var and re-export the verdict. The job itself needs nothing
    from the repo, hence ``permissions: {}``.
    """
    workflow = _load_workflow(name="publish-pypi-on-tag.yml")
    job = workflow["jobs"]["mirror-token"]

    assert_that(job["permissions"]).is_equal_to({})
    assert_that(job["needs"]).contains("github-release")
    assert_that(job["outputs"]["has_token"]).is_equal_to(
        "${{ steps.probe.outputs.has_token }}",
    )

    step = next(s for s in _job_steps(workflow, job="mirror-token") if "run" in s)
    assert_that(step["id"]).is_equal_to("probe")
    assert_that(step["env"]["TOKEN"]).is_equal_to(
        "${{ secrets.MIRROR_REPO_TOKEN }}",
    )
    run = step["run"]
    assert_that(run).contains("has_token=true")
    assert_that(run).contains("has_token=false")
    assert_that(run).contains("$GITHUB_OUTPUT")
    assert_that(run).contains("$GITHUB_STEP_SUMMARY")


def test_mirror_token_guard_warns_when_the_secret_is_absent() -> None:
    """The skip is loud: an annotation plus a step-summary line (#2622)."""
    workflow = _load_workflow(name="publish-pypi-on-tag.yml")
    step = next(s for s in _job_steps(workflow, job="mirror-token") if "run" in s)
    message = "MIRROR_REPO_TOKEN is not set; lintro-pre-commit mirror bump skipped"

    run = step["run"]
    assert_that(run).contains(f'msg="{message}"')
    assert_that(run).contains('echo "::warning::${msg}"')
    assert_that(run).contains('echo "${msg}." >>"$GITHUB_STEP_SUMMARY"')


def test_mirror_release_is_gated_on_the_token_guard() -> None:
    """The mirror call waits for the guard and runs only when it says true.

    Without this the job fails every tag at checkout with "Input required and
    not supplied: token", reddening an otherwise complete release run (#2622).
    The ``actions-v`` recursion guard stays alongside the new condition.
    """
    workflow = _load_workflow(name="publish-pypi-on-tag.yml")
    job = workflow["jobs"]["mirror-release"]

    assert_that(job["needs"]).contains("github-release", "mirror-token")
    assert_that(job["if"]).contains(
        "needs.mirror-token.outputs.has_token == 'true'",
    )
    assert_that(job["if"]).contains("!startsWith(github.ref_name, 'actions-v')")


def test_mirror_release_job_is_read_only_in_source_repo() -> None:
    """Cross-repo writes use MIRROR_REPO_TOKEN; source-repo perms stay read-only."""
    workflow = _load_workflow(name="mirror-release.yml")

    assert_that(workflow["permissions"]).is_equal_to({})
    assert_that(workflow["jobs"]["mirror-bump"]["permissions"]).is_equal_to(
        {"contents": "read"},
    )


def test_mirror_release_hardens_runner_with_blocked_egress() -> None:
    """First step hardens the runner and blocks egress to an allowlist."""
    workflow = _load_workflow(name="mirror-release.yml")
    first = _job_steps(workflow, job="mirror-bump")[0]

    assert_that(first["uses"]).starts_with("step-security/harden-runner@")
    assert_that(first["with"]["egress-policy"]).is_equal_to("block")
    endpoints = first["with"]["allowed-endpoints"]
    assert_that(endpoints).contains("pypi.org:443")
    assert_that(endpoints).contains("files.pythonhosted.org:443")
    assert_that(endpoints).contains("api.github.com:443")


def test_mirror_release_actions_are_sha_pinned() -> None:
    """Every third-party action in the mirror workflow is pinned to a commit SHA."""
    workflow = _load_workflow(name="mirror-release.yml")
    uses = [
        step["uses"]
        for step in _job_steps(workflow, job="mirror-bump")
        if "uses" in step
    ]

    assert_that(uses).is_not_empty()
    for ref in uses:
        assert_that(_SHA_PIN_RE.search(ref)).described_as(ref).is_not_none()


def test_mirror_release_uses_cross_repo_token() -> None:
    """The mirror checkout and publish step authenticate with MIRROR_REPO_TOKEN."""
    workflow = _load_workflow(name="mirror-release.yml")
    steps = _job_steps(workflow, job="mirror-bump")

    checkout = next(
        step
        for step in steps
        if step.get("with", {}).get("repository") == "lgtm-hq/lintro-pre-commit"
    )
    assert_that(checkout["with"]["token"]).contains("secrets.MIRROR_REPO_TOKEN")

    publish = next(
        step for step in steps if "publish-mirror-release.sh" in step.get("run", "")
    )
    assert_that(publish["env"]["GH_TOKEN"]).contains("secrets.MIRROR_REPO_TOKEN")


def test_mirror_release_skips_prereleases() -> None:
    """Wheel-dependent steps are guarded on stable, non-prerelease releases."""
    workflow = _load_workflow(name="mirror-release.yml")
    steps = _job_steps(workflow, job="mirror-bump")
    guard = "steps.resolve.outputs.is_prerelease == 'false'"

    for needle in ("wait-for-pypi-wheel.sh", "publish-mirror-release.sh"):
        step = next(s for s in steps if needle in s.get("run", ""))
        assert_that(step["if"]).contains(guard)
        # The tag itself is the only prerelease signal on the call path: there
        # is no release event payload to read `prerelease` from (#2599).
        assert_that(step["if"]).does_not_contain("github.event.release")
        assert_that(step.get("env", {})).contains_key("LINTRO_VERSION")

    mirror_checkout = next(
        s
        for s in steps
        if s.get("with", {}).get("repository") == "lgtm-hq/lintro-pre-commit"
    )
    assert_that(mirror_checkout["if"]).contains(guard)
    assert_that(mirror_checkout["if"]).does_not_contain("github.event.release")

    setup_python = [
        s for s in steps if s.get("uses", "").startswith("actions/setup-python@")
    ]
    assert_that(setup_python).is_empty()


def test_mirror_release_scripts_are_executable() -> None:
    """The CI scripts referenced by the mirror workflow exist and are executable."""
    scripts = (
        _REPO_ROOT / "scripts" / "ci" / "mirror" / "resolve-version.sh",
        _REPO_ROOT / "scripts" / "ci" / "mirror" / "wait-for-pypi-wheel.sh",
        _REPO_ROOT / "scripts" / "ci" / "mirror" / "publish-mirror-release.sh",
        _REPO_ROOT / "scripts" / "ci" / "mirror" / "bump_pin.py",
    )
    for script in scripts:
        assert_that(script.exists()).described_as(str(script)).is_true()
        assert_that(script.stat().st_mode & 0o111).described_as(
            f"{script} is not executable",
        ).is_not_zero()


# --- Binary build job timeouts (#1702) ---------------------------------------
#
# No job in the former build-binary.yml set timeout-minutes, so every binary
# build inherited GitHub's 6-hour default. The Linux x64 Nuitka compile twice
# hung until runner loss at ~57 min (v0.80.4, v0.91.24), silently desyncing
# the npm publish and Homebrew chain via `needs:`.


@pytest.mark.parametrize("workflow_name", _BINARY_WORKFLOWS)
def test_binary_workflows_every_job_declares_timeout_minutes(
    workflow_name: str,
) -> None:
    """Every binary-stage job is bounded instead of inheriting the 6h default.

    Args:
        workflow_name: The binary workflow under test.
    """
    workflow = _load_workflow(name=workflow_name)
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


def test_build_binary_job_timeout_covers_compile_and_smoke() -> None:
    """The job deadline must cover compile + smoke + diagnostic headroom.

    The smoke-test step can use 20 minutes on its own. A 35-minute job with a
    25-minute compile bound can cancel a valid smoke test before that step's
    own timeout fires.
    """
    workflow = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    for job_id in ("build-macos", "build-linux"):
        job = workflow["jobs"][job_id]
        steps = job["steps"]
        compile_timeout = next(
            step["timeout-minutes"]
            for step in steps
            if step.get("name") == "Build binary"
        )
        smoke_timeout = next(
            step["timeout-minutes"]
            for step in steps
            if step.get("name") == "Smoke-test tool registry"
        )
        headroom = job["timeout-minutes"] - compile_timeout - smoke_timeout
        assert_that(headroom).described_as(job_id).is_greater_than_or_equal_to(10)


# #2579: the macOS x86_64 leg and the lipo'd universal binary are gone. The
# release ships exactly three binaries, the tap dispatch carries one macOS
# checksum, and the npm distribution has no darwin-x64 package - an Intel Mac
# gets a pointer to Homebrew/PyPI from the launcher instead. Every list that
# spells those platforms out is pinned here so none of them can drift back.

_RELEASE_BINARY_ARTIFACTS = (
    "lintro-macos-arm64",
    "lintro-linux-x64",
    "lintro-linux-arm64",
)

_NPM_PLATFORM_KEYS = ("darwin-arm64", "linux-arm64", "linux-x64")


def test_build_binary_ships_exactly_three_platform_binaries() -> None:
    """The binary stages build macOS arm64 plus both Linux arches, nothing else.

    No x86_64 macOS leg, no universal job, no ``arch`` input to select either:
    the job lists of both stages, the macOS matrix, the publish matrix and the
    caller's ``with:`` blocks are all pinned so a partial revert of #2579 is
    caught here rather than on a tag.
    """
    workflow = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    assert_that(set(workflow["jobs"])).is_equal_to(
        {"generate-man-page", "build-macos", "build-linux"},
    )
    publish = _load_workflow(name=_PUBLISH_BINARIES_WORKFLOW)
    # PR (c) of #2562: the release reusable attaches the binaries from the
    # gate's artifact, so the publish stage is the tap dispatch alone.
    assert_that(set(publish["jobs"])).is_equal_to(
        {"get-release-info", "homebrew-dispatch"},
    )

    macos = workflow["jobs"]["build-macos"]
    assert_that(macos["strategy"]["matrix"]).is_equal_to({"arch": ["arm64"]})
    assert_that(str(macos["runs-on"])).does_not_contain("intel")
    assert_that(str(macos["runs-on"])).does_not_contain("x86_64")

    linux = workflow["jobs"]["build-linux"]
    linux_arches = sorted(
        entry["arch"] for entry in linux["strategy"]["matrix"]["include"]
    )
    assert_that(linux_arches).is_equal_to(["arm64", "x64"])

    for workflow_name in _BINARY_WORKFLOWS:
        text = (_REPO_ROOT / ".github" / "workflows" / workflow_name).read_text(
            encoding="utf-8",
        )
        for stale in (
            "lintro-macos-x86_64",
            "sha256-x86_64",
            "universal",
            "lipo",
            "macos-15-intel",
        ):
            # Only the #2579 rationale comment may mention the dropped leg.
            occurrences = [
                line
                for line in text.splitlines()
                if stale in line and not line.lstrip().startswith("#")
            ]
            assert_that(occurrences).described_as(
                f"{stale!r} must not appear outside comments in {workflow_name}",
            ).is_empty()

    caller = _load_workflow(name="publish-pypi-on-tag.yml")
    for job_id, callee in (
        ("build-binaries", _BUILD_BINARY_WORKFLOW),
        ("homebrew-tap", _PUBLISH_BINARIES_WORKFLOW),
    ):
        job = caller["jobs"][job_id]
        assert_that(job["uses"]).described_as(job_id).is_equal_to(
            f"./.github/workflows/{callee}",
        )
        assert_that(job["with"]).described_as(job_id).is_equal_to(
            {"release_tag": "${{ github.ref_name }}"},
        )


def test_homebrew_dispatch_carries_one_macos_checksum() -> None:
    """The tap dispatch sends the arm64 checksum and no x86_64 one (#2579).

    The formula's Intel branch installs from PyPI and needs no asset checksum,
    so a second value here would either be fabricated or read from an artifact
    no job produces any more.
    """
    workflow = _load_workflow(name=_PUBLISH_BINARIES_WORKFLOW)
    job = workflow["jobs"]["homebrew-dispatch"]
    # The binaries are on the release before this workflow is even called
    # (github-release is upstream of the caller job); the checksum comes from
    # the manifest the release gate wrote after verifying the asset (#2562).
    assert_that(job["needs"]).is_equal_to(["get-release-info"])
    by_name = {step.get("name"): step for step in job["steps"]}

    download = by_name["Download release manifest"]
    assert_that(download["uses"]).contains("actions/download-artifact@")
    assert_that(download["with"]).is_equal_to(
        {"name": "release-manifest", "path": "manifest/"},
    )

    read = by_name["Read checksum from the release manifest"]
    assert_that(read["id"]).is_equal_to("checksums")
    assert_that(read["run"]).contains("scripts/ci/release-gate/read_manifest_sha.sh")
    assert_that(read["run"]).contains("lintro-macos-arm64")
    assert_that(read["env"]["MANIFEST"]).is_equal_to("manifest/release-manifest.json")
    assert_that(read["run"]).does_not_contain("x86_64")

    dispatch = by_name["Dispatch formula update"]
    assert_that(dispatch["uses"]).contains("trigger-homebrew-update@")
    payload = dispatch["with"]
    assert_that(payload["binary-arm64-sha"]).is_equal_to(
        "${{ steps.checksums.outputs.arm64_sha256 }}",
    )
    assert_that(payload).does_not_contain_key("binary-x86-sha")
    assert_that(set(payload)).is_equal_to(
        {"formula", "version", "pypi-package", "binary-arm64-sha", "token"},
    )

    # The build-macos matrix is what makes ``sha256-arm64`` exist at all.
    build = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    macos_arches = build["jobs"]["build-macos"]["strategy"]["matrix"]["arch"]
    assert_that(macos_arches).is_equal_to(["arm64"])


def _quoted_strings_in_block(text: str, *, start: str, end: str) -> list[str]:
    """Return every quoted string between two markers of a source file.

    Args:
        text: The file contents.
        start: Substring that opens the block (the first occurrence is used).
        end: Substring that closes the block, searched after ``start``.

    Returns:
        The quoted literals in the block, in order.
    """
    head = text.index(start)
    tail = text.index(end, head + len(start))
    return re.findall(r"""["']([^"']+)["']""", text[head + len(start) : tail])


def test_npm_platform_map_has_no_intel_macos_package() -> None:
    """Every npm platform list agrees on the three shipped platforms (#2579).

    The staging map, the version-sync list, the publish order, the on-disk
    package tree, the meta-package's optional dependencies, the release
    download list and the resolver's map are seven spellings of one fact. If
    any of them kept ``darwin-x64`` the tag run would fail on a missing asset
    or publish a meta-package pointing at a platform package that never ships.
    """
    npm_scripts = _REPO_ROOT / "scripts" / "ci" / "npm"

    stage = (npm_scripts / "stage_binaries.py").read_text(encoding="utf-8")
    stage_pairs = re.findall(
        r'"(lintro-[a-z0-9_-]+)":\s*"([a-z0-9-]+)"',
        stage.partition("BINARY_MAP")[2].partition("}")[0],
    )
    assert_that(dict(stage_pairs)).is_equal_to(
        {
            "lintro-macos-arm64": "darwin-arm64",
            "lintro-linux-arm64": "linux-arm64",
            "lintro-linux-x64": "linux-x64",
        },
    )
    assert_that(sorted(dict(stage_pairs))).is_equal_to(
        sorted(_RELEASE_BINARY_ARTIFACTS),
    )

    download = (npm_scripts / "download_release_binaries.sh").read_text(
        encoding="utf-8",
    )
    assert_that(
        sorted(_quoted_strings_in_block(download, start="binaries=(", end=")")),
    ).is_equal_to(sorted(_RELEASE_BINARY_ARTIFACTS))

    sync = (npm_scripts / "sync_npm_version.py").read_text(encoding="utf-8")
    assert_that(
        sorted(_quoted_strings_in_block(sync, start="PLATFORM_PACKAGES = (", end=")")),
    ).is_equal_to(sorted(_NPM_PLATFORM_KEYS))

    publish = (npm_scripts / "publish_packages.sh").read_text(encoding="utf-8")
    publish_order = _quoted_strings_in_block(publish, start="PACKAGES=(", end=")")
    assert_that(publish_order).is_equal_to([*sorted(_NPM_PLATFORM_KEYS), "lintro"])

    npm_dir = _REPO_ROOT / "npm"
    on_disk = sorted(child.name for child in npm_dir.iterdir() if child.is_dir())
    assert_that(on_disk).is_equal_to(sorted([*_NPM_PLATFORM_KEYS, "lintro"]))

    meta = json.loads(
        (npm_dir / "lintro" / "package.json").read_text(encoding="utf-8"),
    )
    assert_that(sorted(meta["optionalDependencies"])).is_equal_to(
        sorted(f"@lgtm-hq/lintro-{key}" for key in _NPM_PLATFORM_KEYS),
    )

    resolver = (npm_dir / "lintro" / "lib" / "resolve.js").read_text(encoding="utf-8")
    resolver_map = dict(
        re.findall(
            r"'([a-z0-9-]+)':\s*'(@lgtm-hq/lintro-[a-z0-9-]+)'",
            resolver.partition("PLATFORM_PACKAGES = Object.freeze({")[2].partition(
                "});",
            )[0],
        ),
    )
    assert_that(resolver_map).is_equal_to(
        {key: f"@lgtm-hq/lintro-{key}" for key in _NPM_PLATFORM_KEYS},
    )

    # The Intel pointer: a documented dead end, not a silent one.
    hints = resolver.partition("UNSUPPORTED_PLATFORM_HINTS = Object.freeze({")[
        2
    ].partition("});")[0]
    assert_that(hints).contains("'darwin-x64'")
    assert_that(hints).contains("brew install lintro")
    assert_that(hints).contains("pip install lintro")


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


# --- Idempotent binary release jobs (#2435) ----------------------------------
#
# A rerun used to rebuild from scratch and then delete the published asset
# before uploading its replacement; a runner kill in that window stripped
# lintro-linux-x64 off v0.147.3. The jobs now reuse a checksum-verified asset
# and swap uploads instead of overwriting them.

_REUSE_GUARD = "steps.reuse.outputs.reuse != 'true'"
# #2562: the attest step joins the skipped set. The same-run checksum that
# authorises a reuse is written only after the attestation succeeded on the
# earlier attempt, so the reused bytes are already attested.
_REUSE_SKIPPED_STEPS = (
    "Build binary",
    "Verify binary",
    "Smoke-test tool registry",
    "Finalize binary",
    "Attest build provenance",
)
# These must keep running on reuse so a later attempt still finds the binary
# and its checksum among the run artifacts.
_REUSE_UNGATED_STEPS = (
    "Upload artifact",
    "Save SHA256 to file",
    "Upload SHA256 file",
)


def test_build_binary_checks_for_a_reusable_release_asset() -> None:
    """Both per-arch jobs consult the release before compiling.

    The check runs before ``Build binary`` and passes the platform's asset
    name, the same-run checksum artifact, and the finalized destination path.
    """
    workflow = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    expected_args = {
        "build-macos": (
            "lintro-macos-$BUILD_ARCH",
            "sha256-$BUILD_ARCH",
            "dist/nuitka/lintro-macos-$BUILD_ARCH",
        ),
        "build-linux": (
            "lintro-linux-$BUILD_ARCH",
            "sha256-linux-$BUILD_ARCH",
            "dist/nuitka/lintro-linux-$BUILD_ARCH",
        ),
    }
    for job_id, (asset, artifact, dest) in expected_args.items():
        steps = workflow["jobs"][job_id]["steps"]
        names = [step.get("name") for step in steps]
        by_name = {step.get("name"): step for step in steps}

        check = by_name["Check for reusable release asset"]
        assert_that(check["id"]).described_as(job_id).is_equal_to("reuse")
        assert_that(check["run"]).described_as(job_id).contains(
            "scripts/build/reuse_release_asset.sh",
        )
        for token in (asset, artifact, dest):
            assert_that(check["run"]).described_as(job_id).contains(token)
        assert_that(check["env"]).described_as(job_id).contains_key(
            "GH_TOKEN",
            "RELEASE_TAG",
            "BUILD_ARCH",
        )
        assert_that(names.index("Check for reusable release asset")).described_as(
            job_id,
        ).is_less_than(names.index("Build binary"))


def test_build_binary_skips_the_rebuild_when_the_asset_is_reused() -> None:
    """Reuse skips build/verify/smoke/finalize but never the artifact uploads.

    Verify and smoke-test are safe to skip only because the checksum that
    authorised the reuse came from a same-run artifact written after those two
    steps passed on an earlier attempt of the same job.
    """
    workflow = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    for job_id in ("build-macos", "build-linux"):
        by_name = {step.get("name"): step for step in workflow["jobs"][job_id]["steps"]}
        for step_name in _REUSE_SKIPPED_STEPS:
            assert_that(_normalize_github_expr(by_name[step_name]["if"])).described_as(
                f"{job_id}:{step_name}",
            ).is_equal_to(_REUSE_GUARD)
        for step_name in _REUSE_UNGATED_STEPS:
            assert_that(by_name[step_name].get("if")).described_as(
                f"{job_id}:{step_name}",
            ).is_none()


def test_build_binary_save_sha256_falls_back_to_the_reused_checksum() -> None:
    """Finalize binary is skipped on reuse, so its output cannot be the only source."""
    workflow = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    for job_id in ("build-macos", "build-linux"):
        by_name = {step.get("name"): step for step in workflow["jobs"][job_id]["steps"]}
        sha_env = by_name["Save SHA256 to file"]["env"]["SHA256"]
        # The exact expression, not just both names: the order matters (the
        # freshly built checksum wins) and `||` is what makes the reuse value a
        # fallback rather than an override.
        assert_that(_normalize_github_expr(sha_env)).described_as(
            job_id,
        ).is_equal_to(
            "${{ steps.sha256.outputs.sha256 || steps.reuse.outputs.sha256 }}",
        )


def test_build_binary_jobs_may_read_their_own_run_artifacts() -> None:
    """The reuse check needs the release (contents) and the run's artifacts.

    Since #2562 the build jobs are read-only on contents and hold the two
    attestation scopes instead; the publish upload legs hold ``contents:
    write`` and the same ``actions: read``.
    """
    workflow = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    for job_id in ("build-macos", "build-linux"):
        permissions = workflow["jobs"][job_id]["permissions"]
        assert_that(permissions).described_as(job_id).is_equal_to(
            {
                "contents": "read",
                "actions": "read",
                "id-token": "write",
                "attestations": "write",
            },
        )

    publish = _load_workflow(name=_PUBLISH_BINARIES_WORKFLOW)
    permissions = publish["jobs"]["homebrew-dispatch"]["permissions"]
    assert_that(permissions).is_equal_to({"contents": "read"})


def test_build_stage_never_holds_contents_write() -> None:
    """No job in the build stage can write to the repository or a release.

    The point of the split (#2562) is that a binary is built, verified and
    attested by jobs that cannot publish it. Since PR (c) the only
    ``contents: write`` in the tag pipeline is the github-release call; no
    job of either binary workflow holds it.
    """
    build = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    assert_that(build["permissions"]).is_equal_to({})
    for job_id, job in build["jobs"].items():
        grant = _effective_grant(job=job, workflow=build)
        assert_that(_granted_level(grant, scope="contents")).described_as(
            job_id,
        ).is_less_than(_PERMISSION_LEVELS["write"])

    publish = _load_workflow(name=_PUBLISH_BINARIES_WORKFLOW)
    assert_that(publish["permissions"]).is_equal_to({})
    writers = {
        job_id
        for job_id, job in publish["jobs"].items()
        if _granted_level(_effective_grant(job=job, workflow=publish), scope="contents")
        >= _PERMISSION_LEVELS["write"]
    }
    assert_that(writers).is_empty()


def test_build_binaries_attest_the_finalized_binary() -> None:
    """Each build job attests the finalized binary and hard-fails on error.

    The attestation must cover the exact file the artifact upload ships, sit
    after ``Finalize binary`` (so the subject is the renamed, final binary)
    and before ``Upload artifact``, and carry no ``continue-on-error``: an
    unattested binary must fail the build rather than ship (#2562).
    """
    workflow = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    for job_id in ("build-macos", "build-linux"):
        steps = workflow["jobs"][job_id]["steps"]
        names = [step.get("name") for step in steps]
        by_name = {step.get("name"): step for step in steps}

        attest = by_name["Attest build provenance"]
        assert_that(str(attest["uses"])).described_as(job_id).starts_with(
            "actions/attest-build-provenance@",
        )
        assert_that(_SHA_PIN_RE.search(str(attest["uses"]))).described_as(
            job_id,
        ).is_not_none()
        assert_that(attest).described_as(job_id).does_not_contain_key(
            "continue-on-error",
        )
        assert_that(attest["with"]["subject-path"]).described_as(job_id).is_equal_to(
            by_name["Upload artifact"]["with"]["path"],
        )
        assert_that(names.index("Finalize binary")).described_as(job_id).is_less_than(
            names.index("Attest build provenance"),
        )
        assert_that(names.index("Attest build provenance")).described_as(
            job_id,
        ).is_less_than(names.index("Upload artifact"))
        # The reuse invariant behind _REUSE_SKIPPED_STEPS: the same-run
        # checksum is written and uploaded only after the attestation, so a
        # checksum match on a later attempt proves the bytes are attested.
        for checksum_step in ("Save SHA256 to file", "Upload SHA256 file"):
            assert_that(names.index("Attest build provenance")).described_as(
                f"{job_id}: attest must precede {checksum_step}",
            ).is_less_than(names.index(checksum_step))

    # Whole-workflow sweep: no attest step anywhere in the build stage may be
    # best-effort, whatever it is called.
    for job_id, job in workflow["jobs"].items():
        for step in job.get("steps") or []:
            if "attest-build-provenance" in str(step.get("uses", "")):
                assert_that(step.get("continue-on-error")).described_as(
                    f"{job_id}:{step.get('name')}",
                ).is_none()


def test_binary_artifacts_are_retained_for_the_recovery_window() -> None:
    """Every release artifact of the build stage is retained for 90 days.

    Seven days was shorter than the time a broken release can sit before a
    publish rerun needs the built and attested bytes (#2562, policy window
    lgtm-hq/lgtm-ci#962). Failure diagnostics keep the short retention.
    """
    workflow = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    retained: dict[str, int] = {}
    for job in workflow["jobs"].values():
        for step in job.get("steps") or []:
            if not str(step.get("uses", "")).startswith("actions/upload-artifact@"):
                continue
            retained[str(step["with"]["name"])] = int(step["with"]["retention-days"])

    assert_that(retained).is_equal_to(
        {
            "lintro-man-page": 90,
            "lintro-macos-${{ matrix.arch }}": 90,
            "sha256-${{ matrix.arch }}": 90,
            "memory-diagnostics-macos-${{ matrix.arch }}": 7,
            "lintro-linux-${{ matrix.arch }}": 90,
            "sha256-linux-${{ matrix.arch }}": 90,
            "memory-diagnostics-linux-${{ matrix.arch }}": 7,
        },
    )


def test_publish_binaries_receives_one_named_secret() -> None:
    """The publish call passes exactly the tap dispatch token, not ``inherit``.

    ``secrets: inherit`` would hand the call every org/repo secret; the callee
    needs one, declares it on its ``workflow_call`` interface, and uses no
    other secret besides ``GITHUB_TOKEN`` (#2562 review).
    """
    caller = _load_workflow(name="publish-pypi-on-tag.yml")
    job = caller["jobs"]["homebrew-tap"]
    # Built from parts: the value is a GitHub expression, not a credential,
    # and assembling it keeps the mapping literal out of bandit's B105 net.
    tap_dispatch_key = "HOMEBREW_TAP_DISPATCH_TOKEN"
    expected_expression = "${{ secrets." + tap_dispatch_key + " }}"
    assert_that(job["secrets"]).is_equal_to({tap_dispatch_key: expected_expression})
    build_job = caller["jobs"]["build-binaries"]
    assert_that(build_job).does_not_contain_key("secrets")

    callee = _load_workflow(name=_PUBLISH_BINARIES_WORKFLOW)
    declared = callee["on"]["workflow_call"]["secrets"]
    assert_that(set(declared)).is_equal_to({tap_dispatch_key})
    assert_that(declared[tap_dispatch_key]["required"]).is_true()
    for workflow_name in _BINARY_WORKFLOWS:
        text = (_REPO_ROOT / ".github" / "workflows" / workflow_name).read_text(
            encoding="utf-8",
        )
        used = set(re.findall(r"secrets\.([A-Za-z_][A-Za-z0-9_]*)", text))
        assert_that(used - {"GITHUB_TOKEN"}).described_as(workflow_name).is_subset_of(
            set(declared),
        )


def test_binary_publish_jobs_allowlist_the_artifact_service() -> None:
    """Every hardened publish job that moves an artifact can reach the service.

    Block-mode harden-runner denies ``actions/download-artifact`` its hops to
    ``pipelines.actions.githubusercontent.com`` and
    ``results-receiver.actions.githubusercontent.com`` unless both are
    allowlisted, and the failure only shows on a tag run (#2562 review).
    """
    for workflow_name in _BINARY_WORKFLOWS:
        workflow = _load_workflow(name=workflow_name)
        for job_id, job in workflow["jobs"].items():
            steps = job.get("steps") or []
            harden = next(
                (
                    step
                    for step in steps
                    if str(step.get("uses", "")).startswith("step-security/")
                ),
                None,
            )
            assert_that(harden).described_as(f"{workflow_name}:{job_id}").is_not_none()
            assert harden is not None
            assert_that(harden["with"]["egress-policy"]).is_equal_to("block")
            moves_artifact = any(
                str(step.get("uses", "")).startswith(
                    ("actions/upload-artifact@", "actions/download-artifact@"),
                )
                for step in steps
            )
            if not moves_artifact:
                continue
            endpoints = str(harden["with"]["allowed-endpoints"]).split()
            assert_that(endpoints).described_as(f"{workflow_name}:{job_id}").contains(
                "pipelines.actions.githubusercontent.com:443",
                "results-receiver.actions.githubusercontent.com:443",
            )


def _job_ancestors(workflow: dict[str, Any], *, job_id: str) -> set[str]:
    """Return every job ``job_id`` transitively depends on via ``needs``.

    Args:
        workflow: Parsed workflow document.
        job_id: The job whose upstream closure is wanted.

    Returns:
        The transitive ``needs`` closure, excluding ``job_id`` itself.
    """
    ancestors: set[str] = set()
    pending = [job_id]
    while pending:
        current = pending.pop()
        needs = workflow["jobs"][current].get("needs") or []
        if isinstance(needs, str):
            needs = [needs]
        for upstream in needs:
            if upstream not in ancestors:
                ancestors.add(str(upstream))
                pending.append(str(upstream))
    return ancestors


def test_build_stage_reaches_pypi_upload_only_through_the_gate() -> None:
    """Build jobs feed the PyPI upload only via release-gate, never wait on it.

    PR (a)/(b) asserted no build job was upstream of ``pypi-upload`` while
    the builds still ran after the gate. PR (c) puts them ahead of it by
    design, so the invariant becomes: the upload's only direct dependency is
    the gate, every build job is upstream of the gate, and no build job has
    the upload (or anything published) among its own ancestors.
    """
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    assert_that(publish["jobs"]["pypi-upload"]["needs"]).is_equal_to(["release-gate"])
    gate_upstream = _job_ancestors(publish, job_id="release-gate")
    assert_that(gate_upstream).contains(*_BUILD_STAGE_JOBS)
    for job_id in (*_BUILD_STAGE_JOBS, "classify-tag"):
        ancestors = _job_ancestors(publish, job_id=job_id)
        assert_that(ancestors).described_as(job_id).does_not_contain(
            "release-gate",
            *_PUBLISH_JOBS,
        )
    # The build calls run for prereleases too (artifacts only); the publish
    # calls stay stable-only.
    build_if = _normalize_github_expr(str(publish["jobs"]["build-binaries"]["if"]))
    assert_that(build_if).does_not_contain("is_prerelease")
    publish_if = _normalize_github_expr(str(publish["jobs"]["homebrew-tap"]["if"]))
    assert_that(publish_if).contains(
        "needs.classify-tag.outputs.is_prerelease == 'false'",
    )


def test_binary_release_scripts_are_executable() -> None:
    """The #2435 scripts the binary stages reference exist and are executable."""
    scripts = (
        _REPO_ROOT / "scripts" / "build" / "reuse_release_asset.sh",
        _REPO_ROOT / "scripts" / "build" / "upload_release_asset.sh",
    )
    for script in scripts:
        assert_that(script.exists()).described_as(str(script)).is_true()
        assert_that(script.stat().st_mode & 0o111).described_as(
            f"{script} is not executable",
        ).is_not_zero()


def test_memory_sampler_tees_its_output_into_the_step_log() -> None:
    """Sampler evidence reaches stdout, not only the failure-only artifact.

    ``start`` tees a baseline snapshot into its own step log, so a runner kill
    still leaves the memory state the compile began from. The interval samples
    reach a human only through ``Stop memory sampler`` (``if: always()``, which
    replays the log) or the ``if: failure()`` artifact, neither of which runs
    when the runner itself is killed. Both steps must therefore stay wired, and
    the artifact upload must stay failure-only rather than becoming the sole
    channel.
    """
    # What actually reaches stdout is asserted against the running script in
    # tests/scripts/test_memory_sampler.py; this only pins the workflow side,
    # where the sampler steps must stay wired and the artifact upload must stay
    # failure-only rather than becoming the sole channel.
    workflow = _load_workflow(name=_BUILD_BINARY_WORKFLOW)
    for job_id in ("build-macos", "build-linux"):
        by_name = {step.get("name"): step for step in workflow["jobs"][job_id]["steps"]}
        assert_that(by_name["Upload memory diagnostics"]["if"]).described_as(
            job_id,
        ).is_equal_to("failure()")
        assert_that(by_name["Stop memory sampler"]["if"]).described_as(
            job_id,
        ).is_equal_to("always()")


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


def test_docker_ci_queues_main_without_changing_pr_supersession() -> None:
    """Docker CI must retain main runs while PR pushes supersede stale work."""
    docker_ci = _load_workflow(name="docker-ci.yml")
    workflow_concurrency = docker_ci["concurrency"]
    assert_that(workflow_concurrency["group"]).is_equal_to(
        "docker-ci-${{ github.ref }}",
    )
    assert_that(_normalize_github_expr(workflow_concurrency["queue"])).is_equal_to(
        "${{ github.ref == 'refs/heads/main' && 'max' || 'single' }}",
    )
    expected_cancel = "${{ github.ref != 'refs/heads/main' }}"
    assert_that(
        _normalize_github_expr(workflow_concurrency["cancel-in-progress"]),
    ).is_equal_to(expected_cancel)

    docker_build_concurrency = docker_ci["jobs"]["docker-build"]["concurrency"]
    assert_that(docker_build_concurrency["group"]).is_equal_to(
        "docker-build-${{ github.ref }}",
    )
    assert_that(
        _normalize_github_expr(docker_build_concurrency["cancel-in-progress"]),
    ).is_equal_to(expected_cancel)


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


_BUNDLED_RUST_COMPONENT_PACKAGES = (
    "rust-lang/rust-clippy",
    "rust-lang/rustfmt",
)


def test_renovate_does_not_track_rustfmt_or_clippy_independently() -> None:
    """rustfmt/clippy pins are toolchain readouts, not Renovate knobs (#2205).

    Independent managers open unmergeable PRs (#1605) because those tags are
    source milestones, not installable artifacts. rustc remains the single
    rust-family manager; component records bump in the same toolchain PR.
    """
    config = json.loads(
        (_REPO_ROOT / "renovate.json").read_text(encoding="utf-8"),
    )
    managers = config.get("customManagers") or []
    tracked = {
        manager.get("packageNameTemplate")
        for manager in managers
        if manager.get("packageNameTemplate")
    }
    for package in _BUNDLED_RUST_COMPONENT_PACKAGES:
        assert_that(tracked).does_not_contain(package)

    rustc = [
        manager
        for manager in managers
        if manager.get("packageNameTemplate") == "rust-lang/rust"
    ]
    assert_that(rustc).is_length(1)
    assert_that(rustc[0]["description"]).contains("#2205")

    grouped_components = [
        package
        for package in _BUNDLED_RUST_COMPONENT_PACKAGES
        if any(
            package in (rule.get("matchPackageNames") or [])
            for rule in config.get("packageRules") or []
        )
    ]
    assert_that(grouped_components).is_empty()

    match_strings = " ".join(
        " ".join(manager.get("matchStrings") or []) for manager in managers
    )
    assert_that(match_strings).does_not_contain("ToolName.CLIPPY")
    assert_that(match_strings).does_not_contain("ToolName.RUSTFMT")

    assert_that(TOOL_VERSIONS[ToolName.CLIPPY]).is_equal_to(
        TOOL_VERSIONS[ToolName.RUSTC],
    )

    versions = (_REPO_ROOT / "lintro" / "_tool_versions.py").read_text(
        encoding="utf-8",
    )
    assert_that(versions).contains("bump only alongside rustc (#2205)")


def test_renovate_does_not_track_cppcheck() -> None:
    """The cppcheck pin follows Debian's package, not upstream's tags.

    Cppcheck ships no portable single binary, so both the tools image and the
    app-image ``install-tools.sh`` bridge install Debian's package. The
    manifest-vs-image gate requires the installed version to *equal* the
    manifest version, so a Renovate-driven bump to an upstream tag apt cannot
    supply would fail CI permanently rather than merely lag. The pin moves
    only when the ``python:3.14-slim`` base image changes Debian release.
    """
    config = json.loads(
        (_REPO_ROOT / "renovate.json").read_text(encoding="utf-8"),
    )
    managers = config.get("customManagers") or []

    tracked = {
        manager.get("packageNameTemplate")
        for manager in managers
        if manager.get("packageNameTemplate")
    }
    assert_that(tracked).does_not_contain("danmar/cppcheck")

    match_strings = " ".join(
        " ".join(manager.get("matchStrings") or []) for manager in managers
    )
    assert_that(match_strings).does_not_contain("ToolName.CPPCHECK")

    grouped = [
        package
        for package in ("cppcheck", "danmar/cppcheck")
        if any(
            package in (rule.get("matchPackageNames") or [])
            for rule in config.get("packageRules") or []
        )
    ]
    assert_that(grouped).is_empty()

    versions = (_REPO_ROOT / "lintro" / "_tool_versions.py").read_text(
        encoding="utf-8",
    )
    assert_that(versions).contains("NOT Renovate-managed")


# Every workflow file carrying a pinned release reference, and how many sites
# it must carry. Hard-coding the counts is deliberate: asserting only that the
# surviving references agree would stay green if a refactor deleted all but one
# pin, which is exactly the drift this guard exists to catch (#1751).
#
# dogfood-nightly.yml is deliberately absent: it resolves its image at run time
# now (#2602) and must carry no pin at all — see
# ``test_dogfood_nightly_carries_no_hard_coded_release_pin``.
_PINNED_IMAGE_SITES = {
    # One: docker-ci carries the pin in a single workflow-level
    # `env: LINTRO_FORK_FALLBACK_IMAGE` that every fork-fallback consumer
    # reads (#2297).
    "docker-ci.yml": 1,
}


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


# --- AI CLI contract Tier 1 required-check safety (#1119 / #1609) -----------
#
# Epic #1609 shipped the free flag-surface check with the intent that it becomes
# a required gate. Requiring the context before ``merge_group:`` exists arms the
# #1196 absent-required-check merge-queue trap. These constants and the test
# below pin the Tier 1 job to the same always-report shape as the dependency
# vulnerability gate.

_AI_CONTRACT_WORKFLOW = "ai-contract-tests.yml"
_AI_CONTRACT_TIER1_JOB = "tier1-flag-surface"
_AI_CONTRACT_TIER1_CONTEXT = "🧾 AI CLI Flag Surface (Tier 1)"
_AI_CONTRACT_TIER2_JOB = "tier2-invocation-smoke"
_AI_REVIEW_WORKFLOW = "ai-review.yml"
_AI_REVIEW_JOB = "ai-review"
_AI_CONTRACT_GATE_ENV = "AI_CONTRACT_SECRETS_ALLOWED"
#: The clause the dogfood review uses to select its anthropic lane. Tier 2
#: has no provider variable, so its expressions carry the gate here instead.
_DOGFOOD_ANTHROPIC_PROVIDER_CLAUSE = (
    "(vars.LINTRO_AI_PROVIDER || 'anthropic') == 'anthropic'"
)
#: Names of the CLI-behaviour flags the review pins for the agent binaries.
#: Matched by shape rather than listed, so a fourth flag is mirrored without
#: anyone remembering to extend a tuple.
_CLI_BEHAVIOUR_NAME_RE = re.compile(r"^(LINTRO_CLI_|CLAUDE_CODE_|DISABLE_)")

#: The ``secrets.X`` / ``vars.X`` names an expression reads. Tier 2 must read
#: the same ones, whatever gating it wraps around them.
_EXPRESSION_REFERENCE_RE = re.compile(r"\b(?:secrets|vars)\.[A-Za-z_][A-Za-z0-9_]*")

#: Dogfood env the smoke deliberately does not mirror, each because Tier 2 does
#: not do the thing it configures. Kept explicit: a *new* credential or
#: variable in the review step is not on this list, so it fails the mirror test
#: until someone either wires it into Tier 2 or records why it stays here.
_DOGFOOD_ONLY_ENV = {
    # `gh` fetches the PR diff for the review; Tier 2 fetches no diff.
    "GH_TOKEN",
    # The App token `--post` writes the review comment with; Tier 2 posts
    # nothing.
    "GITHUB_TOKEN",
    # Review orchestration. The contract suite builds each provider itself and
    # drives all three lanes in one run, so it has no provider to select, no
    # master switch to flip and no transport to choose.
    "LINTRO_AI_ENABLED",
    "LINTRO_AI_PROVIDER",
    "LINTRO_AI_TRANSPORT",
    # A spend ceiling for a whole review; the smoke is one trivial prompt per
    # lane.
    "LINTRO_AI_MAX_COST_USD",
    # Review-state artifact upload (#2173); the smoke persists nothing.
    "ACTIONS_RUNTIME_TOKEN",
    "ACTIONS_RESULTS_URL",
}
#: Matches a ``host:port`` endpoint inside a harden-runner allowlist or inside
#: the dogfood job's per-provider egress expressions.
_EGRESS_ENDPOINT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.*-]*:\d+")


def _ai_contract_tier1_job() -> dict[str, Any]:
    """Return the Tier 1 AI CLI flag-surface job definition.

    Returns:
        The ``tier1-flag-surface`` job mapping.
    """
    workflow = _load_workflow(name=_AI_CONTRACT_WORKFLOW)
    return cast(dict[str, Any], workflow["jobs"][_AI_CONTRACT_TIER1_JOB])


def test_ai_contract_tier1_is_required_check_safe() -> None:
    """Tier 1 must always report its context (#1119 / #1196).

    A ``paths:`` filter, or a job-level ``if:``, would stop the context from
    ever being created — which deadlocks the merge queue the moment the
    context is added to the ``checks-py-lintro`` ruleset. Tier 1 is cheap
    enough to run unconditionally (help probes only), so it stays a plain job
    with no path filter and no job-level ``if:``.
    """
    workflow = _load_workflow(name=_AI_CONTRACT_WORKFLOW)
    triggers = workflow["on"]

    assert_that(triggers).contains_key(_GITHUB_PULL_REQUEST_EVENT)
    assert_that(triggers).contains_key("merge_group")
    for event in (_GITHUB_PULL_REQUEST_EVENT, "merge_group"):
        assert_that(triggers[event] or {}).does_not_contain_key("paths")
        assert_that(triggers[event] or {}).does_not_contain_key("paths-ignore")
    assert_that(triggers["merge_group"]["types"]).contains("checks_requested")

    job = _ai_contract_tier1_job()
    assert_that(job["name"]).is_equal_to(_AI_CONTRACT_TIER1_CONTEXT)
    assert_that(job).does_not_contain_key("if")
    # A skipped reusable *caller* collapses its nested contexts, so the gate
    # must stay a plain job that always reports its own check run.
    assert_that(job).does_not_contain_key("uses")
    assert_that(job).contains_key("runs-on")

    # The README's admin PUT recipe is the third copy of the context string;
    # pin it to the constant so a job rename cannot leave the recipe stale.
    readme = (_REPO_ROOT / ".github" / "workflows" / "README.md").read_text(
        encoding="utf-8",
    )
    assert_that(readme).contains(_AI_CONTRACT_TIER1_CONTEXT)


def _ai_contract_tier2_job() -> dict[str, Any]:
    """Return the Tier 2 AI CLI invocation-smoke job definition.

    Returns:
        The ``tier2-invocation-smoke`` job mapping.
    """
    workflow = _load_workflow(name=_AI_CONTRACT_WORKFLOW)
    return cast(dict[str, Any], workflow["jobs"][_AI_CONTRACT_TIER2_JOB])


def _harden_runner_endpoints(*, job: dict[str, Any]) -> set[str]:
    """Return the endpoints a job's harden-runner step allows.

    Args:
        job: The job mapping whose first ``step-security`` step is read.

    Returns:
        The set of ``host:port`` endpoints on the allowlist.
    """
    harden = next(
        step
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("step-security/")
    )
    assert_that(harden["with"]["egress-policy"]).is_equal_to("block")
    return set(str(harden["with"]["allowed-endpoints"]).split())


def _dogfood_provider_egress() -> set[str]:
    """Return every host the dogfood review's per-provider egress vars carry.

    Derived from the ``AI_REVIEW_*_EGRESS`` job-level expressions rather than
    from a literal list, so a new provider lane cannot be added to the review
    without the Tier 2 assertion below noticing.

    Returns:
        The union of the per-provider ``host:port`` endpoints.
    """
    review = _load_workflow(name=_AI_REVIEW_WORKFLOW)
    env = review["jobs"][_AI_REVIEW_JOB]["env"]
    endpoints: set[str] = set()
    for name, value in env.items():
        if not str(name).endswith("_EGRESS"):
            continue
        endpoints.update(_EGRESS_ENDPOINT_RE.findall(str(value)))
    return endpoints


def _dogfood_review_step_env() -> dict[str, str]:
    """Return the dogfood review step's env mapping.

    Returns:
        The env mapping of the step that runs ``run-ai-review.sh``.
    """
    review = _load_workflow(name=_AI_REVIEW_WORKFLOW)
    step = next(
        step
        for step in review["jobs"][_AI_REVIEW_JOB]["steps"]
        if "run-ai-review.sh" in str(step.get("run", ""))
        and "--locate-prior-state" not in str(step.get("run", ""))
    )
    return {name: str(value) for name, value in step["env"].items()}


def _mirrored_dogfood_env(env: dict[str, str]) -> dict[str, str]:
    """Return the dogfood env Tier 2 has to carry too.

    Derived by shape rather than listed: anything the review authenticates or
    configures its agent binaries with — a secret, a repo variable, or one of
    the CLI-behaviour flags — is something the smoke must mirror if it is to
    prove the credential and the mode the review actually runs on. Everything
    the review needs for work Tier 2 does not do is named in
    :data:`_DOGFOOD_ONLY_ENV` with its reason.

    Args:
        env: The dogfood review step's env mapping.

    Returns:
        The subset of *env* Tier 2 must mirror, by name.
    """
    return {
        name: value
        for name, value in env.items()
        if name not in _DOGFOOD_ONLY_ENV
        and (
            "secrets." in value
            or "vars." in value
            or _CLI_BEHAVIOUR_NAME_RE.match(name)
        )
    }


def _tier2_expression_from_dogfood(expression: str) -> str:
    """Rewrite a dogfood anthropic expression into its Tier 2 equivalent.

    The dogfood review chooses between its anthropic configurations on a
    provider variable Tier 2 does not have — Tier 2 always drives every lane —
    so the provider clause is the one and only difference: Tier 2 puts its
    trusted-event gate there instead. Everything else, in particular the
    ``ZAI_BASE_URL`` selection between the subscription token and the gateway
    token, must survive the rewrite untouched.

    Args:
        expression: The normalised dogfood expression.

    Returns:
        The normalised expression Tier 2 must carry for the same variable.
    """
    return expression.replace(
        _DOGFOOD_ANTHROPIC_PROVIDER_CLAUSE,
        f"env.{_AI_CONTRACT_GATE_ENV} == 'true'",
    )


def test_ai_contract_tier2_mirrors_dogfood_egress_and_gates_its_secrets() -> None:
    """Tier 2 must reach every dogfood lane's hosts, with gated credentials.

    Egress: the dogfood review allowlists provider hosts one lane at a time
    because a single run picks one provider. Tier 2 drives all three lanes in
    one job, so its allowlist must be a superset of that per-provider union —
    otherwise a lane that authenticates for the review dies here on blocked
    egress instead (#2481; the Codex subscription hosts are the case that
    reddened every lane).

    Secrets: Tier 2 runs on dispatch only today (#2600 removed the cron that
    reached it unwatched), but the round-trip work in #2515 adds a
    ``pull_request`` trigger. Every provider credential is
    therefore routed through one gate expression that also demands a
    same-repository head, so a fork PR resolves each secret to the empty
    string rather than reading it.
    """
    job = _ai_contract_tier2_job()

    dogfood = _dogfood_provider_egress()
    assert_that(dogfood).described_as("dogfood per-provider egress").is_not_empty()
    assert_that(_harden_runner_endpoints(job=job)).described_as(
        "Tier 2 egress must cover every dogfood provider lane",
    ).contains(*sorted(dogfood))

    # Exact, not substring: an added `|| github.event_name == 'push'` would
    # slip past independent contains() checks while widening what can read a
    # provider credential.
    gate = _normalize_github_expr(str(job["env"][_AI_CONTRACT_GATE_ENV]))
    assert_that(gate).is_equal_to(
        "${{ github.event_name == 'workflow_dispatch'"
        f" || github.event.{_GITHUB_PULL_REQUEST_EVENT}"
        ".head.repo.full_name == github.repository }}",
    )

    secret_env = {
        f"{step.get('name')} / {name}": _normalize_github_expr(str(value))
        for step in job["steps"]
        for name, value in (step.get("env") or {}).items()
        if "secrets." in str(value)
    }
    assert_that(secret_env).described_as(
        "Tier 2 must inject provider secrets",
    ).is_not_empty()
    for where, expression in secret_env.items():
        assert_that(expression).described_as(where).contains(
            f"env.{_AI_CONTRACT_GATE_ENV} == 'true'",
        )


def test_ai_contract_tier2_mirrors_every_dogfood_credential_and_cli_setting() -> None:
    """Tier 2 must carry the review's credentials and CLI settings, unchanged.

    A green Tier 2 is only evidence about the review if the two jobs drive the
    binaries the same way. Three shapes of mirror are checked, all derived from
    ai-review.yml rather than restated here, so a new dogfood env var fails
    this test until Tier 2 mirrors it or :data:`_DOGFOOD_ONLY_ENV` records why
    it should not:

    * The anthropic credential expressions select between the subscription
      token and the z.ai gateway on ``ZAI_BASE_URL`` (#2472 lane 2). Tier 2
      repeats the whole selection with one substitution — dogfood picks on its
      provider variable, Tier 2 (which always drives every lane) puts its
      trusted-event gate in that position.
    * The CLI-behaviour flags are plain literals — ``LINTRO_CLI_BARE: never``
      decides whether the anthropic lane proves an OAuth session or an API key
      — so they must match exactly.
    * Everything else carrying a secret or a variable must at least name the
      same one and ride the gate.
    """
    dogfood_env = _dogfood_review_step_env()
    tier2_env = next(
        step["env"]
        for step in _ai_contract_tier2_job()["steps"]
        if "run-ai-contract-tests.sh" in str(step.get("run", ""))
    )

    mirrored = _mirrored_dogfood_env(dogfood_env)
    assert_that(mirrored).described_as("derived dogfood mirror set").is_not_empty()

    for name, dogfood_value in mirrored.items():
        assert_that(tier2_env).described_as(
            f"Tier 2 must mirror the dogfood env var {name}",
        ).contains_key(name)
        tier2_value = _normalize_github_expr(str(tier2_env[name]))
        normalized = _normalize_github_expr(dogfood_value)

        if _DOGFOOD_ANTHROPIC_PROVIDER_CLAUSE in normalized:
            expected = _tier2_expression_from_dogfood(normalized)
            # The rewrite must have found the provider clause; otherwise the
            # comparison would silently assert dogfood equals itself.
            assert_that(expected).described_as(name).does_not_contain(
                _DOGFOOD_ANTHROPIC_PROVIDER_CLAUSE,
            )
            assert_that(tier2_value).described_as(name).is_equal_to(expected)
        elif "${{" not in normalized:
            assert_that(tier2_value).described_as(name).is_equal_to(normalized)
        else:
            for reference in _EXPRESSION_REFERENCE_RE.findall(normalized):
                assert_that(tier2_value).described_as(name).contains(reference)
            assert_that(tier2_value).described_as(name).contains(
                f"env.{_AI_CONTRACT_GATE_ENV} == 'true'",
            )


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
        {"contents": "read", "actions": "read"},
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
        "fulcio.sigstore.dev:443",
        "rekor.sigstore.dev:443",
        "token.actions.githubusercontent.com:443",
    ):
        assert_that(endpoints).contains(endpoint)


_DOGFOOD_TOOL_OPTIONS_RE = re.compile(
    r"pydoclint:timeout=\d+,[^\s]+osv_scanner:check_suppressions=[^\s,]+",
)
_EXPECTED_DOGFOOD_TOOL_OPTIONS = (
    "pydoclint:timeout=120,black:timeout=120,bandit:timeout=120,prettier:timeout=120,"
    "mypy:timeout=120,gitleaks:timeout=120,typos:timeout=120,semgrep:timeout=600,"
    "osv_scanner:check_suppressions=false"
)


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
    dispatch = workflow["on"]["workflow_dispatch"]
    assert_that(dispatch["inputs"]).contains_key("force_publish")

    promote_script = (
        _REPO_ROOT / "scripts" / "ci" / "promote-ci-docker-images.sh"
    ).read_text(encoding="utf-8")
    assert_that(promote_script).contains("check-tools-manifest-staleness.sh")


# --- Reusable-workflow permission wiring (#2484) ---------------------------
#
# GitHub refuses a called workflow that requests a permission its caller job
# does not grant, and it refuses it before any job starts: the run reports
# `startup_failure` with no jobs and no logs. #2440 added `actions: read` to
# build-binary.yml's compile jobs without adding it to the `homebrew-tap` job
# that calls them, and every tag from v0.151.2 through v0.152.6 died that way.
# Nothing caught it because the only caller is the tag pipeline, which never
# runs on a PR or on main, and a `workflow_dispatch` of the callee uses its own
# token so the mismatch does not apply.

_PERMISSION_LEVELS = {"none": 0, "read": 1, "write": 2}
_LOCAL_WORKFLOW_CALL_PREFIX = "./.github/workflows/"


def _permission_level(value: object) -> int:
    """Map a workflow permission value to a comparable access level.

    Args:
        value: The raw YAML value of a single permission scope.

    Returns:
        ``0`` for none, ``1`` for read, ``2`` for write.

    Raises:
        AssertionError: If the value is not one GitHub accepts, so a typo or
            a novel level fails the walk instead of reading as "none".
    """
    if not isinstance(value, str) or value.strip().lower() not in _PERMISSION_LEVELS:
        raise AssertionError(f"unknown permission value {value!r}")
    return _PERMISSION_LEVELS[value.strip().lower()]


def test_permission_level_rejects_unknown_values() -> None:
    """A misspelt or novel permission value fails loudly, never as ``none``."""
    for bad in ("writ", None, True, 1):
        with pytest.raises(AssertionError, match="unknown permission value"):
            _permission_level(bad)


def _normalize_permissions(raw: object) -> dict[str, int] | None:
    """Normalize a ``permissions:`` value to per-scope access levels.

    Args:
        raw: A ``permissions`` mapping, the ``read-all``/``write-all``
            shorthand, or ``None`` when the block is absent.

    Returns:
        A scope-to-level mapping, or ``None`` when no block was declared.
        The ``read-all``/``write-all`` shorthands return ``{"*": level}``,
        which :func:`_granted_level` reads as a floor for every scope.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        shorthand = raw.strip().lower()
        if shorthand in {"read-all", "write-all"}:
            return {"*": _permission_level(shorthand.removesuffix("-all"))}
        return {}
    if isinstance(raw, dict):
        return {str(scope): _permission_level(value) for scope, value in raw.items()}
    return {}


def _granted_level(grant: dict[str, int], *, scope: str) -> int:
    """Return the access level ``grant`` gives ``scope``.

    Args:
        grant: A normalized permission mapping.
        scope: The permission scope being looked up.

    Returns:
        The granted level, falling back to any ``read-all``/``write-all``
        wildcard and then to ``0``.
    """
    return max(grant.get(scope, 0), grant.get("*", 0))


def _effective_grant(
    *,
    job: dict[str, Any],
    workflow: dict[str, Any],
) -> dict[str, int]:
    """Return the permissions a job actually holds.

    A job without its own ``permissions`` block inherits the workflow-level
    block. Most workflows here declare ``permissions: {}`` at the top, so the
    empty grant is the usual outcome - but not all of them do
    (``docker-build-publish.yml`` declares a top-level ``contents: read``),
    which is exactly why the workflow-level block is consulted rather than
    assumed empty. When neither the job nor the workflow declares a block at
    all, GitHub falls back to the default ``GITHUB_TOKEN`` grant, which this
    repository's org/repo setting models as ``contents: read``; that default
    is returned instead of an empty grant, while an explicit
    ``permissions: {}`` stays empty.

    Args:
        job: The parsed job mapping.
        workflow: The parsed workflow that contains ``job``.

    Returns:
        A scope-to-level mapping.
    """
    job_level = _normalize_permissions(job.get("permissions"))
    if job_level is not None:
        return job_level
    workflow_level = _normalize_permissions(workflow.get("permissions"))
    if workflow_level is not None:
        return workflow_level
    return {"contents": _PERMISSION_LEVELS["read"]}


def _local_workflow_calls(
    *,
    workflow: dict[str, Any],
) -> list[tuple[str, dict[str, Any], str]]:
    """Find the jobs of ``workflow`` that call a local reusable workflow.

    Args:
        workflow: The parsed caller workflow.

    Returns:
        ``(job id, job mapping, callee file name)`` for each local call.
    """
    calls: list[tuple[str, dict[str, Any], str]] = []
    for job_id, job in (workflow.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        uses = job.get("uses")
        if isinstance(uses, str) and uses.startswith(_LOCAL_WORKFLOW_CALL_PREFIX):
            calls.append(
                (
                    str(job_id),
                    job,
                    uses.removeprefix(_LOCAL_WORKFLOW_CALL_PREFIX),
                ),
            )
    return calls


def _permission_shortfalls(
    *,
    caller_label: str,
    caller_grant: dict[str, int],
    callee_name: str,
    depth: int,
) -> list[str]:
    """Collect every permission a callee requests beyond its caller's grant.

    Recurses one level so a callee that itself calls a local workflow is
    checked against the grant it received, not against the root caller's.

    Args:
        caller_label: Human-readable ``workflow::job`` label of the caller.
        caller_grant: The caller job's effective permissions.
        callee_name: File name of the called workflow.
        depth: Remaining recursion depth; ``0`` stops the walk.

    Returns:
        One message per scope the callee requests and the caller withholds.
    """
    callee = _load_workflow(name=callee_name)
    shortfalls: list[str] = []
    for callee_job_id, callee_job in (callee.get("jobs") or {}).items():
        if not isinstance(callee_job, dict):
            continue
        requested = _effective_grant(job=callee_job, workflow=callee)
        for scope, level in requested.items():
            if level == 0:
                continue
            granted = _granted_level(caller_grant, scope=scope)
            if granted < level:
                shortfalls.append(
                    f"{caller_label} grants {scope}="
                    f"{'none' if granted == 0 else 'read'} but "
                    f"{callee_name}::{callee_job_id} requests {scope}="
                    f"{'read' if level == 1 else 'write'}",
                )
        if depth > 0:
            nested_uses = callee_job.get("uses")
            if isinstance(nested_uses, str) and nested_uses.startswith(
                _LOCAL_WORKFLOW_CALL_PREFIX,
            ):
                shortfalls.extend(
                    _permission_shortfalls(
                        caller_label=f"{callee_name}::{callee_job_id}",
                        caller_grant=requested,
                        callee_name=nested_uses.removeprefix(
                            _LOCAL_WORKFLOW_CALL_PREFIX,
                        ),
                        depth=depth - 1,
                    ),
                )
    return shortfalls


@pytest.fixture
def workflow_files() -> list[Path]:
    """Return every workflow definition under ``.github/workflows``.

    Returns:
        Sorted paths of the repository's workflow YAML files.
    """
    workflows_dir = _REPO_ROOT / ".github" / "workflows"
    return sorted(
        path
        for path in workflows_dir.iterdir()
        if path.suffix in {".yml", ".yaml"} and path.is_file()
    )


@pytest.fixture
def parsed_workflows(workflow_files: list[Path]) -> dict[str, dict[str, Any]]:
    """Parse every workflow once, keyed by file name.

    Args:
        workflow_files: The workflow paths to parse.

    Returns:
        A mapping of file name to parsed workflow.
    """
    return {path.name: _load_workflow(name=path.name) for path in workflow_files}


def test_reusable_workflow_callers_grant_what_callees_request(
    parsed_workflows: dict[str, dict[str, Any]],
) -> None:
    """Every local `uses:` caller grants at least what the callee requests.

    A shortfall is not a job failure but a whole-run `startup_failure` with no
    logs to point at it, so it has to be caught here. See #2484.

    Args:
        parsed_workflows: Every workflow in the repository, parsed.
    """
    shortfalls: list[str] = []
    for workflow_name, workflow in parsed_workflows.items():
        for job_id, job, callee_name in _local_workflow_calls(workflow=workflow):
            shortfalls.extend(
                _permission_shortfalls(
                    caller_label=f"{workflow_name}::{job_id}",
                    caller_grant=_effective_grant(job=job, workflow=workflow),
                    callee_name=callee_name,
                    depth=1,
                ),
            )
    assert_that(shortfalls).described_as("caller/callee permission gaps").is_empty()


def test_reusable_workflow_permission_check_covers_the_release_pipeline(
    parsed_workflows: dict[str, dict[str, Any]],
) -> None:
    """The check actually walks the tag pipeline's reusable-workflow calls.

    An empty walk would make the test above pass vacuously, which is exactly
    how #2440's regression stayed invisible.

    Args:
        parsed_workflows: Every workflow in the repository, parsed.
    """
    calls = _local_workflow_calls(
        workflow=parsed_workflows["publish-pypi-on-tag.yml"],
    )
    callees = {callee for _, _, callee in calls}
    assert_that(callees).contains(
        _BUILD_BINARY_WORKFLOW,
        _PUBLISH_BINARIES_WORKFLOW,
        "docker-build-publish.yml",
    )
    for caller_job_id in ("build-binaries",):
        caller_job = next(job for job_id, job, _ in calls if job_id == caller_job_id)
        grant = _effective_grant(
            job=caller_job,
            workflow=parsed_workflows["publish-pypi-on-tag.yml"],
        )
        assert_that(_granted_level(grant, scope="actions")).described_as(
            caller_job_id,
        ).is_greater_than_or_equal_to(_PERMISSION_LEVELS["read"])


def test_permission_shortfalls_detects_a_withheld_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_permission_shortfalls` reports a scope the caller withholds.

    Guards the detector itself against a synthetic callee: without this, a
    detector that always returned an empty list would leave the walk above
    green forever.

    Args:
        monkeypatch: pytest attribute patcher, used to substitute a synthetic
            callee workflow for the on-disk one.
    """
    callee = {
        "permissions": {},
        "jobs": {
            "compile": {"permissions": {"contents": "write", "actions": "read"}},
        },
    }
    monkeypatch.setattr(
        f"{__name__}._load_workflow",
        lambda *, name: callee,
    )
    shortfalls = _permission_shortfalls(
        caller_label="caller.yml::calls",
        caller_grant=_normalize_permissions({"contents": "write"}) or {},
        callee_name="callee.yml",
        depth=1,
    )
    assert_that(shortfalls).is_length(1)
    assert_that(shortfalls[0]).contains("actions=none")
    assert_that(shortfalls[0]).contains("requests actions=read")


def test_permission_shortfalls_detects_a_read_grant_against_a_write_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller granting read against a write request is reported as a gap.

    The withheld-scope test above only exercises the ``none``/``read`` corner
    of the message. The ``read``/``write`` rendering is the shape a callee
    bumping a scope to write produces, and it is the one that startup-fails a
    tag run, so it needs its own synthetic case.

    Args:
        monkeypatch: pytest attribute patcher, used to substitute a synthetic
            callee workflow for the on-disk one.
    """
    callee = {
        "permissions": {},
        "jobs": {
            "compile": {"permissions": {"actions": "write"}},
        },
    }
    monkeypatch.setattr(
        f"{__name__}._load_workflow",
        lambda *, name: callee,
    )
    shortfalls = _permission_shortfalls(
        caller_label="caller.yml::calls",
        caller_grant=_normalize_permissions({"actions": "read"}) or {},
        callee_name="callee.yml",
        depth=1,
    )
    assert_that(shortfalls).is_length(1)
    assert_that(shortfalls[0]).contains("grants actions=read")
    assert_that(shortfalls[0]).contains("requests actions=write")


def test_reusable_workflow_walk_fails_on_the_pre_2518_publish_workflow(
    parsed_workflows: dict[str, dict[str, Any]],
) -> None:
    """The walk reddens on the exact grant the v0.151.2-v0.152.6 tags died on.

    #2440 gave build-binary.yml's compile jobs ``actions: read`` while the
    ``homebrew-tap`` caller still granted only ``contents: write``; #2518 added
    the grant. Since #2562 the compile jobs live in build-binaries.yml behind
    the ``build-binaries`` caller, which carries that ``actions: read`` for
    the reuse check. Replaying the withheld grant against the real callee
    proves the walk catches it rather than passing because nothing on disk is
    broken today.

    Args:
        parsed_workflows: Every workflow in the repository, parsed.
    """
    publish = deepcopy(parsed_workflows["publish-pypi-on-tag.yml"])
    caller_job = publish["jobs"]["build-binaries"]
    pre_2518_grant = {
        scope: value
        for scope, value in caller_job["permissions"].items()
        if scope != "actions"
    }
    assert_that(caller_job["permissions"]).described_as(
        "the fixture only means something while #2518's grant is present",
    ).contains_key("actions")
    caller_job["permissions"] = pre_2518_grant

    shortfalls = _permission_shortfalls(
        caller_label="publish-pypi-on-tag.yml::build-binaries",
        caller_grant=_effective_grant(job=caller_job, workflow=publish),
        callee_name=str(caller_job["uses"]).removeprefix(
            _LOCAL_WORKFLOW_CALL_PREFIX,
        ),
        depth=1,
    )
    assert_that(shortfalls).is_not_empty()
    for message in shortfalls:
        assert_that(message).contains("requests actions=read")
    assert_that(" ".join(shortfalls)).contains(
        f"{_BUILD_BINARY_WORKFLOW}::build-macos",
    )


def test_permission_shortfalls_is_silent_when_the_grant_covers_the_callee(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller granting at least as much as the callee produces no message.

    Args:
        monkeypatch: pytest attribute patcher.
    """
    callee = {
        "permissions": {"contents": "read"},
        "jobs": {"compile": {"permissions": {"actions": "read"}}, "docs": {}},
    }
    monkeypatch.setattr(
        f"{__name__}._load_workflow",
        lambda *, name: callee,
    )
    shortfalls = _permission_shortfalls(
        caller_label="caller.yml::calls",
        caller_grant=_normalize_permissions(
            {"contents": "write", "actions": "read"},
        )
        or {},
        callee_name="callee.yml",
        depth=1,
    )
    assert_that(shortfalls).is_empty()


def test_permission_shortfalls_recurses_into_a_nested_local_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A grandchild's request is checked against the grant its caller received.

    Args:
        monkeypatch: pytest attribute patcher.
    """
    workflows = {
        "middle.yml": {
            "permissions": {},
            "jobs": {
                "relay": {
                    "permissions": {"contents": "write"},
                    "uses": "./.github/workflows/leaf.yml",
                },
            },
        },
        "leaf.yml": {
            "permissions": {},
            "jobs": {"compile": {"permissions": {"packages": "write"}}},
        },
    }
    monkeypatch.setattr(
        f"{__name__}._load_workflow",
        lambda *, name: workflows[name],
    )
    shortfalls = _permission_shortfalls(
        caller_label="root.yml::calls",
        caller_grant=_normalize_permissions({"contents": "write"}) or {},
        callee_name="middle.yml",
        depth=1,
    )
    assert_that(shortfalls).is_length(1)
    assert_that(shortfalls[0]).contains("middle.yml::relay")
    assert_that(shortfalls[0]).contains("requests packages=write")


def test_missing_permissions_block_models_the_token_default() -> None:
    """No block anywhere means GitHub's default grant, not an empty one."""
    default_grant = _effective_grant(job={}, workflow={})
    assert_that(_granted_level(default_grant, scope="contents")).is_equal_to(
        _PERMISSION_LEVELS["read"],
    )
    assert_that(_granted_level(default_grant, scope="packages")).is_equal_to(0)

    explicit_empty = _effective_grant(job={}, workflow={"permissions": {}})
    assert_that(explicit_empty).is_equal_to({})
    inherited = _effective_grant(
        job={},
        workflow={"permissions": {"contents": "read"}},
    )
    assert_that(inherited).is_equal_to({"contents": _PERMISSION_LEVELS["read"]})
    job_overrides = _effective_grant(
        job={"permissions": {}},
        workflow={"permissions": {"contents": "write"}},
    )
    assert_that(job_overrides).is_equal_to({})


def test_permission_shorthands_normalize_to_levels() -> None:
    """``read-all``/``write-all`` and a missing block normalize correctly."""
    assert_that(_normalize_permissions(None)).is_none()
    assert_that(_normalize_permissions({})).is_equal_to({})
    read_all = _normalize_permissions("read-all") or {}
    assert_that(_granted_level(read_all, scope="actions")).is_equal_to(1)
    write_all = _normalize_permissions("write-all") or {}
    assert_that(_granted_level(write_all, scope="packages")).is_equal_to(2)
    explicit = _normalize_permissions({"contents": "read", "id-token": "write"}) or {}
    assert_that(_granted_level(explicit, scope="contents")).is_equal_to(1)
    assert_that(_granted_level(explicit, scope="id-token")).is_equal_to(2)
    assert_that(_granted_level(explicit, scope="actions")).is_equal_to(0)


def test_ai_review_job_has_no_lint_step() -> None:
    """Linter facts come from the untrusted lint job's artifact (#2571).

    The trusted review job holds the posting and provider credentials and
    checks out the base ref, so it must never run lintro's tools or any PR
    code: no lint step, no ``--with-lint``, and no second checkout.
    """
    review = _load_workflow(name=_AI_REVIEW_WORKFLOW)
    job = review["jobs"][_AI_REVIEW_JOB]
    checkouts = 0
    for step in job["steps"]:
        run = str(step.get("run", ""))
        assert_that(run).does_not_contain("--with-lint")
        assert_that(run).does_not_match(r"lintro\s+(chk|check|fmt|format)\b")
        assert_that(str(step.get("uses", ""))).does_not_contain("lgtm-ci/")
        if str(step.get("uses", "")).startswith("actions/checkout@"):
            checkouts += 1
    assert_that(checkouts).is_equal_to(1)
    # `gh run download` of the report needs actions: read, which the job
    # already grants for review-state artifacts.
    assert_that(job["permissions"]["actions"]).is_equal_to("read")


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


# ---------------------------------------------------------------------------
# Weekly provider API smoke (#2600)
# ---------------------------------------------------------------------------
#
# The CLI smoke this replaces failed every Monday from 2026-08-10 and reached
# nobody: it gated nothing, and a scheduled failure is a red X in a run list.
# Three properties are what make the replacement visible, and none of them can
# be seen by reading one job — so they are pinned here.

_API_SMOKE_WORKFLOW = "ai-provider-api-smoke.yml"
_API_SMOKE_JOB = "smoke"
_API_SMOKE_KEY = "ai-provider-api-smoke"
_API_SMOKE_TABLE = (
    _REPO_ROOT / "scripts" / "ci" / "ai_provider_smoke" / "providers.json"
)


def _api_smoke_rows() -> list[dict[str, str]]:
    """Return the committed provider smoke table.

    Read rather than restated: the table is the single site a provider is
    added at, and a test that repeated its rows would stop proving that.

    Returns:
        The table's provider rows.
    """
    data = json.loads(_API_SMOKE_TABLE.read_text(encoding="utf-8"))
    return cast(list[dict[str, str]], data["providers"])


def _api_smoke_matrix() -> list[dict[str, str]]:
    """Return the Actions matrix the smoke table expands to.

    Built by the script the workflow itself runs, so the egress entry a row
    reaches the runner with is the one asserted here.

    Returns:
        The matrix include entries, one per table row.

    Raises:
        RuntimeError: When the runner script cannot be imported.
    """
    spec = importlib.util.spec_from_file_location(
        "ai_provider_run_smoke_wiring",
        _API_SMOKE_TABLE.with_name("run_smoke.py"),
    )
    if spec is None or spec.loader is None:  # pragma: no cover - import plumbing
        msg = "unable to load the provider smoke runner"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    # Registered before execution: the script's frozen dataclass resolves its
    # own module through sys.modules while the class body runs.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    rows = module.load_table(path=_API_SMOKE_TABLE)
    return cast(list[dict[str, str]], module.build_matrix(rows=rows)["include"])


def test_provider_api_smoke_runs_weekly_and_on_demand() -> None:
    """The live provider signal must be scheduled, since nothing else is.

    Tier 2 of the CLI contract suite gave up its cron in the same change, so
    this workflow carries the weekly cadence for every live provider check in
    the repo. A dispatch trigger stays alongside it so a fix can be proven
    without waiting a week.
    """
    triggers = _load_workflow(name=_API_SMOKE_WORKFLOW)["on"]
    assert_that(triggers).contains_key("schedule")
    assert_that(triggers).contains_key("workflow_dispatch")
    crons = [entry["cron"] for entry in triggers["schedule"]]
    assert_that(crons).is_length(1)
    # Weekly, exactly: a single day-of-week. A range or a list ('1-5', '1,3')
    # would pass a not-a-wildcard check while spending quota several times a
    # week, which is the cost this cadence was chosen to bound.
    day_of_week = crons[0].split()[4]
    assert_that(day_of_week).described_as(
        "the provider smoke must run on exactly one weekday",
    ).matches(r"^[0-6]$")


def test_cli_invocation_smoke_is_manual_only() -> None:
    """Tier 2 must not be reachable by a cron any more (#2600).

    Its credentials are subscription- and session-bound, so it cannot be kept
    green by design; running it unwatched produced a year's worth of red that
    meant nothing. Three places have to agree, or the demotion is cosmetic:
    the workflow declares no schedule, the job gates on dispatch alone, and
    the job name says so where a reader will see it.
    """
    workflow = _load_workflow(name=_AI_CONTRACT_WORKFLOW)
    assert_that(workflow["on"]).does_not_contain_key("schedule")

    job = workflow["jobs"][_AI_CONTRACT_TIER2_JOB]
    assert_that(_normalize_github_expr(str(job["if"]))).is_equal_to(
        "github.event_name == 'workflow_dispatch'",
    )
    assert_that(job["name"]).contains("manual only")

    # The docstring of the tier's own suite is where a contributor learns the
    # cadence; a stale "scheduled" there re-teaches the thing just removed.
    smoke_suite = (
        _REPO_ROOT / "tests" / "contract" / "test_cli_invocation_smoke.py"
    ).read_text(encoding="utf-8")
    assert_that(smoke_suite).contains("manual only")


def test_provider_api_smoke_is_driven_by_the_committed_table() -> None:
    """The matrix, the job name and the egress all come from the table.

    Adding a provider must cost a row and a secret. A matrix written into the
    workflow, or an allowlist that names hosts, would quietly reintroduce the
    workflow edit — and an unlisted host fails the call on blocked egress
    rather than on the provider's own verdict.
    """
    workflow = _load_workflow(name=_API_SMOKE_WORKFLOW)
    job = workflow["jobs"][_API_SMOKE_JOB]

    assert_that(str(job["strategy"]["matrix"])).contains(
        "needs.resolve-table.outputs.matrix",
    )
    assert_that(job["strategy"]["fail-fast"]).is_false()
    assert_that(str(job["name"])).contains("matrix.name")

    harden = next(
        step
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("step-security/")
    )
    allowlist = str(harden["with"]["allowed-endpoints"])
    assert_that(allowlist).described_as(
        "the provider host must come from the matrix row, not a literal list",
    ).contains("${{ matrix.egress }}")
    rows = _api_smoke_rows()
    assert_that(rows).is_not_empty()
    egress = {entry["name"]: entry["egress"] for entry in _api_smoke_matrix()}
    for row in rows:
        parsed = urlparse(row["base_url"])
        host = parsed.hostname
        # Without this, a row whose base_url does not parse yields host None
        # and the check below becomes the vacuous 'None:443' is absent — the
        # guard would pass on exactly the row that breaks egress.
        assert_that(host).described_as(
            f"{row['name']} base_url {row['base_url']!r} must parse to a host",
        ).is_not_none()
        # The allowlist entry is what the row is actually allowed to dial. A
        # row that reaches the matrix without one, or with one naming another
        # host or port, fails the weekly run on blocked egress with an opaque
        # network error instead of the provider's own verdict.
        assert_that(egress).described_as(
            "every table row must reach the matrix with an egress entry",
        ).contains_key(row["name"])
        assert_that(egress[row["name"]]).described_as(
            f"{row['name']} egress must allow its own base_url host",
        ).is_equal_to(f"{host}:{parsed.port or 443}")
        assert_that(allowlist).described_as(
            f"{row['name']} host must not be hard-coded here",
        ).does_not_contain(f"{host}:443")

    resolve = workflow["jobs"]["resolve-table"]["steps"][-1]
    assert_that(str(resolve["run"])).contains("--emit-matrix")


#: The Actions results service, which upload-artifact and download-artifact
#: talk to. Under ``egress-policy: block`` a job that moves an artifact
#: without these on its allowlist fails on blocked egress, and the failure
#: reads as the artifact being absent rather than as a network block.
_ARTIFACT_ENDPOINTS: tuple[str, ...] = (
    "pipelines.actions.githubusercontent.com:443",
    "results-receiver.actions.githubusercontent.com:443",
)


def test_artifact_jobs_allowlist_the_actions_results_service() -> None:
    """Every blocked job here that moves an artifact must be able to reach it.

    The provider smoke uploads its error text and the annotate job downloads
    it; that hop is what puts the provider's own words on the tracker issue,
    so it must not depend on the results service being reachable by accident.

    Scoped to this workflow on purpose: ten pre-existing jobs elsewhere in the
    repo move artifacts under a blocked policy without naming these hosts and
    are green today, so a repo-wide rule belongs in its own change rather than
    riding along here.
    """
    offenders: list[str] = []
    for path in [_REPO_ROOT / ".github" / "workflows" / _API_SMOKE_WORKFLOW]:
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(workflow, dict):
            continue
        for job_name, job in (workflow.get("jobs") or {}).items():
            steps = job.get("steps") or [] if isinstance(job, dict) else []
            uses = [str(step.get("uses", "")) for step in steps]
            moves_artifact = any(
                action.startswith(
                    ("actions/upload-artifact@", "actions/download-artifact@"),
                )
                for action in uses
            )
            harden = next(
                (
                    step
                    for step in steps
                    if str(step.get("uses", "")).startswith("step-security/")
                ),
                None,
            )
            if not moves_artifact or harden is None:
                continue
            with_block = harden.get("with") or {}
            if str(with_block.get("egress-policy", "")) != "block":
                continue
            allowlist = str(with_block.get("allowed-endpoints", ""))
            missing = [host for host in _ARTIFACT_ENDPOINTS if host not in allowlist]
            if missing:
                offenders.append(f"{path.name}:{job_name} missing {missing}")

    assert_that(offenders).described_as(
        "a blocked job that moves an artifact must allowlist the results service",
    ).is_empty()


def test_provider_api_smoke_failures_are_visible_on_main() -> None:
    """A failure must redden main and reach a human, not just this run.

    Two independent channels, because the single channel the CLI smoke had
    (the run list) is what failed for a month: a commit status on main's HEAD,
    and the deduplicated tracker issue. The status step is ``always()`` —
    reporting only on success is how a red job goes unseen.
    """
    workflow = _load_workflow(name=_API_SMOKE_WORKFLOW)
    job = workflow["jobs"][_API_SMOKE_JOB]

    assert_that(job["permissions"]["statuses"]).is_equal_to("write")
    status_step = next(
        step
        for step in job["steps"]
        if "report-commit-status.sh" in str(step.get("run", ""))
    )
    condition = _normalize_github_expr(str(status_step["if"]))
    assert_that(condition).contains("always()")
    assert_that(condition).contains("github.ref == 'refs/heads/main'")

    # What the status script *does* — the context it posts under and the
    # pending-not-green rule for a call that was never made — is asserted by
    # running it against a stubbed `gh` in
    # tests/scripts/test_ai_provider_smoke.py. Pinning its source text here
    # would break on a rename that changes no behaviour, and would equally be
    # satisfied by a comment. This test owns the wiring: that the job runs
    # that script at all, with the permission and the condition it needs.
    assert_that(
        (_REPO_ROOT / "scripts" / "ci" / "ai_provider_smoke")
        .joinpath("report-commit-status.sh")
        .is_file(),
    ).is_true()

    # No job here may swallow its own verdict: the whole workflow exists to
    # make a failure arrive somewhere.
    for name, smoke_job in workflow["jobs"].items():
        assert_that(smoke_job).described_as(name).does_not_contain_key(
            "continue-on-error",
        )
        for step in smoke_job.get("steps", []):
            assert_that(step).described_as(
                f"{name}/{step.get('name')}",
            ).does_not_contain_key("continue-on-error")


def test_provider_api_smoke_files_a_distinctly_labelled_tracker_issue() -> None:
    """Failures reuse the shared filer, under their own label.

    Reusing ``reusable-main-failure-notifier.yml`` is what keeps one dedup
    marker mechanism in the repo rather than two. The label is what keeps a
    credit-exhaustion alarm distinguishable from a generic main failure at a
    glance, so it must not be the plain bug/ci pair every other caller uses.
    """
    workflow = _load_workflow(name=_API_SMOKE_WORKFLOW)
    notify = workflow["jobs"]["notify-failure"]

    assert_that(str(notify["uses"])).contains(
        "lgtm-hq/lgtm-ci/.github/workflows/reusable-main-failure-notifier.yml",
    )
    condition = _normalize_github_expr(str(notify["if"]))
    assert_that(condition).contains("failure()")
    assert_that(condition).contains("github.ref == 'refs/heads/main'")
    assert_that(notify["with"]["workflow-key"]).is_equal_to(_API_SMOKE_KEY)
    labels = str(notify["with"]["failure-issue-labels"]).split(",")
    assert_that(labels).contains("ai-provider-smoke")
    assert_that(notify["permissions"]["issues"]).is_equal_to("write")

    # The error text is what makes the issue actionable, and it reaches the
    # tracker through the notifier's own issue — never through a second filer.
    annotate = workflow["jobs"]["annotate-failure-issue"]
    assert_that(annotate["needs"]).contains("notify-failure")
    assert_that(annotate["permissions"]["issues"]).is_equal_to("write")
    comment_step = next(
        step
        for step in annotate["steps"]
        if "post_error_details.py" in str(step.get("run", ""))
    )
    assert_that(str(comment_step["run"])).contains("--workflow-key")
    details = (
        _REPO_ROOT / "scripts" / "ci" / "ai_provider_smoke" / "post_error_details.py"
    ).read_text(encoding="utf-8")
    assert_that(details).does_not_contain("issue create")


_GHCR_CLEANUP_LANE_JOBS = (
    "prune-untagged",
    "sweep-ci-tags",
    "sweep-sha-tags",
    "sweep-tools-candidates",
    "prune-untagged-base",
)


def test_ghcr_cleanup_reports_its_verdict_on_main() -> None:
    """The weekly prune carries the epic's visibility contract (#2598, #2603).

    The prune failed for five weeks unseen because a scheduled failure is only
    a red X in a run list. The verdict must land on main's HEAD as a commit
    status from a job that runs on every outcome and summarises every lane
    job, and a failure must reach the shared main-failure filer under the
    lane's own label.
    """
    cleanup = _load_workflow(name="ghcr-cleanup.yml")
    jobs = cleanup["jobs"]

    report = jobs["report-status"]
    assert_that(report["needs"]).contains(*_GHCR_CLEANUP_LANE_JOBS)
    condition = _normalize_github_expr(str(report["if"]))
    assert_that(condition).contains("always()")
    assert_that(condition).contains("github.ref == 'refs/heads/main'")
    assert_that(report["permissions"]["statuses"]).is_equal_to("write")
    report_steps = [
        step
        for step in report["steps"]
        if step.get("run") == "scripts/ci/maintenance/report-workflow-commit-status.sh"
    ]
    assert_that(report_steps).is_length(1)
    # The wired script must exist: a rename or move would otherwise only fail
    # at the scheduled run's first execution after it (lintro-review P3).
    assert_that(
        (
            _REPO_ROOT
            / "scripts"
            / "ci"
            / "maintenance"
            / "report-workflow-commit-status.sh"
        ).is_file(),
    ).is_true()
    env = report_steps[0]["env"]
    assert_that(env["STATUS_CONTEXT"]).is_equal_to("ghcr-cleanup")
    # Pin the whole expression: the status must summarise every lane job, so
    # the mapping has to stay the full needs.*.result join, not just mention
    # the wildcard anywhere (lintro-review P3).
    assert_that(str(env["JOB_RESULTS"])).is_equal_to(
        "${{ join(needs.*.result, ' ') }}",
    )

    notify = jobs["notify-failure"]
    assert_that(notify["needs"]).contains(*_GHCR_CLEANUP_LANE_JOBS)
    assert_that(str(notify["uses"])).contains(
        "lgtm-hq/lgtm-ci/.github/workflows/reusable-main-failure-notifier.yml",
    )
    condition = _normalize_github_expr(str(notify["if"]))
    assert_that(condition).contains("failure()")
    assert_that(condition).contains("github.ref == 'refs/heads/main'")
    assert_that(notify["with"]["workflow-key"]).is_equal_to("ghcr-cleanup")
    labels = str(notify["with"]["failure-issue-labels"]).split(",")
    assert_that(labels).contains("ghcr-cleanup")
    assert_that(notify["permissions"]["issues"]).is_equal_to("write")


#: Number-typed ``workflow_call`` inputs of every lgtm-ci reusable this repo
#: calls, read from the callees at the canonical pin (see
#: ``test_lgtm_ci_refs_match_canonical_pin``). Refresh when a pin bump adds a
#: number input; ``timeout-minutes`` is on every reusable and is listed once.
_LGTM_CI_NUMBER_INPUTS: dict[str, frozenset[str]] = {
    "reusable-ghcr-cleanup.yml": frozenset(
        {
            "build-cache-pr-age-days",
            "keep-latest",
            "main-retention-days",
            "min-age-days",
            "prerelease-retention-days",
        },
    ),
    "reusable-build-python-dist.yml": frozenset({"artifact-retention-days"}),
    "reusable-test-python.yml": frozenset({"coverage-threshold"}),
}
_LGTM_CI_NUMBER_INPUTS_COMMON: frozenset[str] = frozenset({"timeout-minutes"})
_BLOCK_SCALAR_WITH = re.compile(r"^      (?P<key>[a-z-]+):\s*[>|][-+]?\s*$")


def test_number_typed_reusable_inputs_are_never_block_scalars() -> None:
    """A number input must receive a number, not a folded string (#2603).

    A ``>-`` block scalar hands the callee the *string* ``'7'`` even when the
    expression inside evaluates to a number, and GitHub rejects the reusable
    call at plan time (``Unexpected value '7'``): the job never exists, no
    log is written, and the run only shows the caller's other jobs. That is
    how both prune legs vanished from ghcr-cleanup run 34754126197. Boolean
    and string inputs tolerate the folded form, so the rule is scoped to the
    number-typed inputs of each callee.
    """
    offenders: list[str] = []
    for path in sorted((_REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        lines = path.read_text(encoding="utf-8").splitlines()
        current_job: str | None = None
        number_keys: frozenset[str] = frozenset()
        for index, line in enumerate(lines, start=1):
            job_match = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
            if job_match:
                current_job = job_match.group(1)
                number_keys = frozenset()
                continue
            uses_match = re.match(
                r"^    uses: lgtm-hq/lgtm-ci/\.github/workflows/([^@\s]+)@",
                line,
            )
            if uses_match:
                callee = uses_match.group(1)
                number_keys = (
                    _LGTM_CI_NUMBER_INPUTS.get(
                        callee,
                        frozenset(),
                    )
                    | _LGTM_CI_NUMBER_INPUTS_COMMON
                )
                continue
            scalar = _BLOCK_SCALAR_WITH.match(line)
            if scalar and scalar.group("key") in number_keys:
                offenders.append(
                    f"{path.name}::{current_job}::{scalar.group('key')}:{index}",
                )
    assert_that(offenders).described_as(
        "number-typed lgtm-ci inputs passed as block scalars",
    ).is_empty()


_RELEASE_IMAGE_JOBS = ("docker-base", "docker-full", "docker-ai")


def test_every_pushed_reusable_docker_call_carries_provenance_and_sbom(
    parsed_workflows: dict[str, dict[str, Any]],
) -> None:
    """Release images carry the same evidence as the tool images (#2630).

    ``reusable-docker.yml`` gates its GitHub attestation on ``provenance``, so
    an opt-out drops the attestation as well as the BuildKit provenance. Every
    call that can push must therefore pass both flags; the backfill dispatch
    shares the release jobs, so it is covered by the same assertion.

    Args:
        parsed_workflows: Every workflow in the repository, parsed.
    """
    missing: dict[str, dict[str, Any]] = {}
    for workflow_name, workflow in parsed_workflows.items():
        for job_id, job in (workflow.get("jobs") or {}).items():
            uses = str(job.get("uses", ""))
            if "reusable-docker.yml" not in uses:
                continue
            with_block = job.get("with") or {}
            push = with_block.get("push", "")
            # YAML ``push: false`` parses to a boolean; only an explicit false
            # or an absent input marks a validate-only job.
            if push is False or str(push).strip().lower() in ("", "false"):
                continue
            evidence = {
                key: with_block.get(key)
                for key in ("provenance", "sbom", "cosign-sign", "scan")
            }
            if not all(evidence[key] is True for key in evidence):
                missing[f"{workflow_name}::{job_id}"] = evidence
            # Execution bypasses defeat the evidence inputs: a
            # ``continue-on-error`` job succeeds past an attestation failure,
            # and a constant-false ``if`` skips production entirely while
            # every ``with`` assertion above still passes.
            assert_that(job.get("continue-on-error")).described_as(
                f"{workflow_name}::{job_id} continue-on-error",
            ).is_none()
            condition = str(job.get("if", "")).strip()
            if condition:
                assert_that(condition.lower()).described_as(
                    f"{workflow_name}::{job_id} if",
                ).is_not_in(("false", "${{ false }}"))
    assert_that(missing).described_as("pushed image jobs lacking evidence").is_empty()

    publish = parsed_workflows["docker-build-publish.yml"]
    for job_id in _RELEASE_IMAGE_JOBS:
        with_block = publish["jobs"][job_id]["with"]
        assert_that(str(with_block["scan-exit-code"])).described_as(job_id).is_equal_to(
            "0",
        )


#: The only ``if`` guards a pushed reusable-docker caller may carry, keyed
#: ``workflow::job`` and compared after GitHub-expression normalization. An
#: absent guard maps to the empty string; any other condition — in
#: particular a constant-false one — must land here through review first.
_EVIDENCE_CALLER_IF_ALLOWLIST: dict[str, str] = {
    "docker-ai-tools-publish.yml::ai-tools-image": "",
    "docker-build-publish.yml::docker-base": (
        "always() && !cancelled() && "
        "(needs.validate-backfill-inputs.result == 'success' || "
        "needs.validate-backfill-inputs.result == 'skipped') && "
        "needs.resolve-endpoints.result == 'success'"
    ),
    "docker-build-publish.yml::docker-full": (
        "always() && !cancelled() && "
        "(needs.validate-backfill-inputs.result == 'success' || "
        "needs.validate-backfill-inputs.result == 'skipped') && "
        "needs.resolve-endpoints.result == 'success' && "
        "needs.docker-base.result == 'success'"
    ),
    "docker-build-publish.yml::docker-ai": (
        "always() && !cancelled() && "
        "(needs.validate-backfill-inputs.result == 'success' || "
        "needs.validate-backfill-inputs.result == 'skipped') && "
        "needs.resolve-endpoints.result == 'success' && "
        "needs.docker-full.result == 'success'"
    ),
    "docker-tools-candidate.yml::candidate-build": (
        "github.actor == 'renovate[bot]' && needs.resolve-pr.result == 'success'"
    ),
    "docker-tools-promote.yml::publish-fallback": (
        "needs.resolve.outputs.action == 'publish' && github.ref == 'refs/heads/main'"
    ),
    "docker-tools-publish.yml::tools-image": "",
}

#: Same contract for the two docker-ci.yml jobs that hold the evidence steps,
#: keyed by ``workflow::job``. A job-level guard such as ``false && always()``
#: skips the whole job before any step runs, which the step allowlist below
#: cannot see (CodeRabbit on #2640), so the job conditions are pinned too.
_EVIDENCE_JOB_IF_ALLOWLIST: dict[str, str] = {
    "docker-ci.yml::docker-build": "!cancelled()",
    "docker-ci.yml::publish": (
        "github.ref == 'refs/heads/main' && "
        "github.event_name == 'push' && "
        "needs.changes.outputs.pipeline != 'false' && "
        "needs.code-quality-gate.outputs.result == 'success' && "
        "needs.code-quality-gate.outputs.infra-flake != 'true'"
    ),
}

#: Same contract for the evidence steps of docker-ci.yml's own build/publish
#: path, keyed by step name: the pushed CI-tag builds, the cosign signature
#: and the two provenance attestations.
_EVIDENCE_STEP_IF_ALLOWLIST: dict[str, str] = {
    "Build and push Docker image (GHCR CI tag)": (
        "needs.changes.outputs.pipeline != 'false' && "
        "steps.fork-check.outputs.is-fork != 'true'"
    ),
    "Build and push Base Docker image (GHCR CI tag)": (
        "needs.changes.outputs.pipeline != 'false' && "
        "steps.fork-check.outputs.is-fork != 'true'"
    ),
    "Sign promoted digests (keyless)": "",
    "Attest build provenance (promoted digest)": "",
    "Attest build provenance (promoted base digest)": "",
}


def test_pushed_docker_callers_run_under_allowlisted_guards(
    parsed_workflows: dict[str, dict[str, Any]],
) -> None:
    """A pushed reusable-docker call runs only under a reviewed guard (#2630).

    The bypass guards in the provenance test reject constant-false ``if``
    values outright; this pins the full non-constant condition of every
    pushed caller against an explicit allowlist, so a new guard that skips
    evidence production cannot slip in unreviewed, and rejects any caller
    that is missing from the allowlist entirely.

    Args:
        parsed_workflows: Every workflow in the repository, parsed.
    """
    seen: set[str] = set()
    for workflow_name, workflow in parsed_workflows.items():
        for job_id, job in (workflow.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            if "reusable-docker.yml" not in str(job.get("uses", "")):
                continue
            push = (job.get("with") or {}).get("push", "")
            if push is False or str(push).strip().lower() in ("", "false"):
                continue
            key = f"{workflow_name}::{job_id}"
            seen.add(key)
            assert_that(
                _normalize_github_expr(str(job.get("if", ""))),
            ).described_as(f"{key} if").is_equal_to(
                _normalize_github_expr(_EVIDENCE_CALLER_IF_ALLOWLIST.get(key, "")),
            )
    assert_that(seen).described_as(
        "pushed reusable-docker callers vs the guard allowlist",
    ).is_equal_to(set(_EVIDENCE_CALLER_IF_ALLOWLIST))


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


def test_main_promotion_attests_the_promoted_digests() -> None:
    """``ghcr.io/lgtm-hq/py-lintro:main`` carries a GitHub attestation (#2630).

    The ``publish`` job promotes ``ci-<run_id>`` digests to ``main``/``sha-*``
    and cosign-signs them; without an attestation step the rolling tags had a
    signature and nothing else. The pushed ``ci-*`` builds must also attach
    BuildKit provenance and an SBOM, because promotion by digest keeps
    exactly what the build attached.
    """
    ci = _load_workflow(name="docker-ci.yml")

    build_steps = ci["jobs"]["docker-build"]["steps"]
    pushed = [
        step
        for step in build_steps
        if "build-push-action" in str(step.get("uses", ""))
        and (step.get("with") or {}).get("push") is True
    ]
    assert_that(pushed).is_length(2)
    for step in pushed:
        with_block = step["with"]
        assert_that(str(with_block.get("provenance"))).described_as(
            step["name"],
        ).is_equal_to("mode=max")
        assert_that(with_block.get("sbom")).described_as(step["name"]).is_true()

    publish = ci["jobs"]["publish"]
    assert_that(publish["permissions"]["attestations"]).is_equal_to("write")
    assert_that(publish["permissions"]["id-token"]).is_equal_to("write")
    attest_steps = [
        step
        for step in publish["steps"]
        if "actions/attest-build-provenance@" in str(step.get("uses", ""))
    ]
    subjects = {
        (step["with"]["subject-name"], str(step["with"]["subject-digest"]))
        for step in attest_steps
    }
    assert_that(subjects).is_equal_to(
        {
            ("ghcr.io/lgtm-hq/py-lintro", "${{ steps.promote.outputs.digest }}"),
            (
                "ghcr.io/lgtm-hq/py-lintro-base",
                "${{ steps.promote-base.outputs.digest }}",
            ),
        },
    )
    for step in attest_steps:
        assert_that(step["with"].get("push-to-registry")).is_true()


# --- #2562 PR (b)/(c): build stage, release gate, publish stage --------------

#: Jobs that only build, verify or attest; none may wait on a publish job.
_BUILD_STAGE_JOBS = ("sbom", "pypi-build", "build-binaries", "docker-build")

#: Jobs that write to a channel; every one must be downstream of release-gate.
_PUBLISH_JOBS = (
    "pypi-upload",
    "github-release",
    "docker-promote",
    "homebrew-tap",
    "npm-publish",
    "mirror-token",
    "mirror-release",
)

_DIST_SIGNER_WORKFLOW = (
    "lgtm-hq/lgtm-ci/.github/workflows/reusable-build-python-dist.yml"
)
_BINARY_SIGNER_WORKFLOW = "lgtm-hq/py-lintro/.github/workflows/build-binaries.yml"

_RELEASE_IMAGE_DIGEST_OUTPUTS = {
    "base-digest": "${{ jobs.docker-base.outputs.digest }}",
    "full-digest": "${{ jobs.docker-full.outputs.digest }}",
    "ai-digest": "${{ jobs.docker-ai.outputs.digest }}",
}

#: Image -> the docker-build output the promote step must pin to.
_PROMOTED_IMAGE_DIGESTS = {
    "ghcr.io/lgtm-hq/py-lintro-base": "${{ needs.docker-build.outputs.base-digest }}",
    "ghcr.io/lgtm-hq/py-lintro": "${{ needs.docker-build.outputs.full-digest }}",
    "ghcr.io/lgtm-hq/py-lintro-ai": "${{ needs.docker-build.outputs.ai-digest }}",
}


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


def test_release_manifest_is_retained_for_the_recovery_window() -> None:
    """The gate's manifest and assets are 90-day artifacts (#2562 PR (c))."""
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    gate = publish["jobs"]["release-gate"]
    steps = gate["steps"]
    uploads = {
        str(s["with"]["name"]): s["with"]
        for s in steps
        if "actions/upload-artifact@" in str(s.get("uses", ""))
    }
    assert_that(set(uploads)).is_equal_to({"release-assets", "release-manifest"})
    for name, with_block in uploads.items():
        assert_that(with_block["retention-days"]).described_as(name).is_equal_to(90)
        assert_that(with_block["if-no-files-found"]).described_as(name).is_equal_to(
            "error",
        )
    write = next(
        s
        for s in steps
        if s.get("run") == "python3 scripts/ci/release-gate/write_manifest.py"
    )
    assert_that(write["env"]["OUTPUT"]).is_equal_to(uploads["release-manifest"]["path"])
    assert_that(write["env"]["ASSETS_DIR"] + "/").is_equal_to(
        uploads["release-assets"]["path"],
    )
    for var, output in (
        ("BASE_DIGEST", "base-digest"),
        ("FULL_DIGEST", "full-digest"),
        ("AI_DIGEST", "ai-digest"),
    ):
        assert_that(write["env"][var]).is_equal_to(
            f"${{{{ needs.docker-build.outputs.{output} }}}}",
        )
    for script in (
        "scripts/ci/write-release-manifest.py",
        "scripts/ci/release-gate/write_manifest.py",
        "scripts/ci/release-gate/verify_artifacts.sh",
        "scripts/ci/release-gate/read_manifest_sha.sh",
    ):
        assert_that(os.access(_REPO_ROOT / script, os.X_OK)).described_as(
            script,
        ).is_true()


# --- #2562 PR (c): the gate and the reorder ----------------------------------


def test_release_gate_needs_every_build_job_and_verifies_per_signer() -> None:
    """release-gate is fed by every build job and fails closed on each check.

    dist/* was attested inside lgtm-ci's build reusable and the binaries
    inside build-binaries.yml; a reusable signs as the called file, so the
    gate passes per-kind signer workflows and never the entry workflow.
    """
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    gate = publish["jobs"]["release-gate"]
    assert_that(set(gate["needs"])).is_equal_to({"classify-tag", *_BUILD_STAGE_JOBS})
    assert_that(gate["permissions"]).is_equal_to(
        {"contents": "read", "attestations": "read"},
    )
    assert_that(_normalize_github_expr(str(gate["if"]))).contains(
        "github.ref_type == 'tag'",
    )
    steps = gate["steps"]
    verify = next(
        s
        for s in steps
        if s.get("run") == "scripts/ci/release-gate/verify_artifacts.sh"
    )
    env = verify["env"]
    assert_that(env["ATTESTATION_REPO"]).is_equal_to("lgtm-hq/py-lintro")
    assert_that(env["DIST_SIGNER_WORKFLOW"]).is_equal_to(_DIST_SIGNER_WORKFLOW)
    assert_that(env["BINARY_SIGNER_WORKFLOW"]).is_equal_to(_BINARY_SIGNER_WORKFLOW)
    assert_that(env).contains_key("GH_TOKEN")
    downloaded = {
        str((s.get("with") or {}).get("name") or (s.get("with") or {}).get("pattern"))
        for s in steps
        if "actions/download-artifact@" in str(s.get("uses", ""))
    }
    assert_that(downloaded).is_equal_to(
        {"python-dist", "lintro-macos-*", "lintro-linux-*", "lintro-man-page"},
    )
    for step in steps:
        if step.get("run") or "upload-artifact" in str(step.get("uses", "")):
            assert_that(step.get("continue-on-error")).described_as(
                str(step.get("name")),
            ).is_none()
            assert_that(step.get("if")).described_as(str(step.get("name"))).is_none()


def test_every_publish_job_is_downstream_of_the_release_gate() -> None:
    """Nothing writes to a channel unless release-gate passed (#2562)."""
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    for job_id in _PUBLISH_JOBS:
        assert_that(_job_ancestors(publish, job_id=job_id)).described_as(
            job_id,
        ).contains("release-gate")


def test_no_job_has_a_step_after_its_irreversible_step() -> None:
    """The PyPI upload is the last step of its job, after attestation checks.

    Once ``pypa/gh-action-pypi-publish`` succeeds no rerun can reach a later
    step (#2618), so nothing may follow it; the attestation check precedes
    it via ``prepare-pypi-upload`` with ``require-attestation``.
    """
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    irreversible = ("pypa/gh-action-pypi-publish@",)
    for job_id, job in publish["jobs"].items():
        steps = job.get("steps") or []
        for index, step in enumerate(steps):
            if any(marker in str(step.get("uses", "")) for marker in irreversible):
                assert_that(index).described_as(job_id).is_equal_to(len(steps) - 1)
    upload = publish["jobs"]["pypi-upload"]
    assert_that(upload["needs"]).is_equal_to(["release-gate"])
    assert_that(upload["environment"]).is_equal_to("pypi")
    assert_that(upload["permissions"]).is_equal_to(
        {"contents": "read", "attestations": "read", "id-token": "write"},
    )
    names = [str(step.get("uses", "")) for step in upload["steps"]]
    assert_that(names[-1]).starts_with("pypa/gh-action-pypi-publish@")
    assert_that(names[-2]).contains("actions/prepare-pypi-upload@")
    prepare = upload["steps"][-2]["with"]
    assert_that(str(prepare["require-attestation"])).is_equal_to("true")
    assert_that(prepare["signer-workflow"]).is_equal_to(_DIST_SIGNER_WORKFLOW)
    assert_that(" ".join(names)).does_not_contain("attest-build-provenance")


def test_github_release_attaches_the_gated_assets_immutably() -> None:
    """The release carries exactly what the gate assembled, never overwritten."""
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    release = publish["jobs"]["github-release"]
    assert_that(release["needs"]).is_equal_to(["pypi-upload"])
    with_block = release["with"]
    assert_that(with_block["artifact-name"]).is_equal_to("release-assets")
    assert_that(with_block["artifact-path"]).is_equal_to("release")
    assert_that(with_block["checksums"]).is_true()
    assert_that(with_block["immutable-assets"]).is_true()
    assert_that(with_block).does_not_contain_key("files")
    assert_that(release["permissions"]).is_equal_to({"contents": "write"})


def test_build_binaries_and_dist_build_ahead_of_the_gate() -> None:
    """The binary build hangs off classify-tag and the dist keeps 90 days."""
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    assert_that(publish["jobs"]["build-binaries"]["needs"]).is_equal_to(
        ["classify-tag"],
    )
    assert_that(
        publish["jobs"]["pypi-build"]["with"]["artifact-retention-days"],
    ).is_equal_to(90)


def test_prerelease_tags_run_the_gate_and_skip_every_channel_publish() -> None:
    """A prerelease proves the build stage and the gate, publishing nothing new."""
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    stable_only = ("docker-promote", "homebrew-tap", "npm-publish")
    for job_id in stable_only:
        condition = _normalize_github_expr(str(publish["jobs"][job_id]["if"]))
        assert_that(condition).described_as(job_id).contains(
            "needs.classify-tag.outputs.is_prerelease == 'false'",
        )
    for job_id in (*_BUILD_STAGE_JOBS, "release-gate", "pypi-upload", "github-release"):
        condition = _normalize_github_expr(str(publish["jobs"][job_id].get("if", "")))
        assert_that(condition).described_as(job_id).does_not_contain("is_prerelease")


def test_homebrew_dispatch_reads_the_arm64_digest_from_the_manifest() -> None:
    """The tap gets the digest the gate verified, after the release exists."""
    publish = _load_workflow(name="publish-pypi-on-tag.yml")
    tap = publish["jobs"]["homebrew-tap"]
    assert_that(set(tap["needs"])).is_equal_to(
        {"classify-tag", "release-gate", "github-release"},
    )
    assert_that(tap["permissions"]).is_equal_to({"contents": "read"})
    callee = _load_workflow(name=_PUBLISH_BINARIES_WORKFLOW)
    dispatch = callee["jobs"]["homebrew-dispatch"]
    by_name = {step.get("name"): step for step in dispatch["steps"]}
    assert_that(by_name["Download release manifest"]["with"]["name"]).is_equal_to(
        "release-manifest",
    )
    payload = by_name["Dispatch formula update"]["with"]
    assert_that(payload["binary-arm64-sha"]).is_equal_to(
        "${{ steps.checksums.outputs.arm64_sha256 }}",
    )
    assert_that(callee["jobs"]).does_not_contain_key("upload-binaries")
    assert_that(callee["jobs"]).does_not_contain_key("upload-man-page")
    # npm no longer waits on Homebrew: both hang off the release.
    assert_that(_job_ancestors(publish, job_id="npm-publish")).does_not_contain(
        "homebrew-tap",
    )
    assert_that(publish["jobs"]["npm-publish"]["needs"]).contains("github-release")


def test_docker_promote_promotes_the_full_release_tag_set() -> None:
    """Each image gets <version>, <major.minor>, <major> and latest (#2658 nit)."""
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
    patterns = [
        "type=semver,pattern={{version}},value=${{ github.ref_name }}",
        "type=semver,pattern={{major}}.{{minor}},value=${{ github.ref_name }}",
        "type=semver,pattern={{major}},value=${{ github.ref_name }}",
    ]
    for step_id, with_block in metas.items():
        assert_that(with_block["images"]).described_as(step_id).is_equal_to(
            expected_images[step_id],
        )
        assert_that(str(with_block["flavor"]).strip()).described_as(
            step_id,
        ).is_equal_to(
            "latest=true",
        )
        tags = [line.strip() for line in str(with_block["tags"]).splitlines() if line]
        assert_that(tags).described_as(step_id).is_equal_to(
            patterns,
        )
