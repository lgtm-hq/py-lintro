"""Shared readers and evaluators for workflow wiring contracts."""

from __future__ import annotations

import ast
import importlib.util
import io
import json
import os
import re
import shlex
import subprocess  # nosec B404 - subprocess runs fixed git argv against this repo
import sys
import tempfile
import tokenize
from collections import Counter
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
    context_conditions: dict[str, bool] | None = None,
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
    for token, value in (context_conditions or {}).items():
        expr = _replace_github_token(expr, token=token, replacement=repr(value))
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
        ".grype.yaml",
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


def _publish_npm_jobs() -> dict[str, Any]:
    """Return the jobs of publish-npm.yml.

    Returns:
        The parsed ``jobs`` mapping.
    """
    return cast(dict[str, Any], _load_workflow(name="publish-npm.yml")["jobs"])


def _publish_npm_step(*, job: str, name: str) -> dict[str, Any]:
    """Return one named step of a publish-npm.yml job.

    Args:
        job: Job id.
        name: Step name.

    Returns:
        The step mapping.
    """
    steps = _publish_npm_jobs()[job]["steps"]
    step = next((s for s in steps if s.get("name") == name), None)
    assert_that(step).described_as(f"{job}: step {name!r} not found").is_not_none()
    assert step is not None  # narrow type for mypy
    return cast(dict[str, Any], step)


_NPM_SET_REUSABLE = "lgtm-hq/lgtm-ci/.github/workflows/reusable-publish-npm-set.yml"

_NPM_PACKAGE_ORDER = ["darwin-arm64", "linux-arm64", "linux-x64", "lintro"]


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


# #2562: the binary workflows are workflow_call only. The former
# build-binary.yml carried a workflow_dispatch repair path whose get-release-info
# resolved the *latest published* release and republished the dispatched ref's
# binaries onto it (#2484). Every publishing step was gated on
# ``inputs.release_tag != '' || inputs.upload_to_release == true`` to keep a
# bare dispatch side-effect free; with the trigger gone the gate and its
# evaluator are gone too. Recovery is lgtm-hq/lgtm-ci#966.

_BINARY_WORKFLOWS = (_BUILD_BINARY_WORKFLOW, _PUBLISH_BINARIES_WORKFLOW)


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


#: Matches a pinned harden-runner ``uses:`` with its ``# vX.Y.Z`` comment.
_HARDEN_RUNNER_PIN_RE = re.compile(
    r"step-security/harden-runner@[0-9a-f]{40}\s+#\s*v(\d+)\.(\d+)\.(\d+)",
)

#: Matches the lgtm-ci reusable lint workflow pin the dogfood callers use.
_LGTM_CI_QUALITY_LINT_PIN_RE = re.compile(
    r"lgtm-hq/lgtm-ci/\.github/workflows/reusable-quality-lint\.yml"
    r"@[0-9a-f]{40}\s+#\s*v(\d+)\.(\d+)\.(\d+)",
)

#: First harden-runner release that allows ``*.githubapp.com`` from GitHub meta.
_HARDEN_RUNNER_GITHUB_META_FLOOR = (2, 21, 1)

#: First lgtm-ci release whose reusable lint workflow pins that harden-runner.
_LGTM_CI_GITHUB_META_FLOOR = (0, 70, 0)


_DELIBERATELY_UNWATCHED = frozenset(
    {
        # Mutate repository state (tags, release PRs) rather than reporting on
        # it, so an automatic rerun could double-tag or resurrect a version PR.
        "Release - Auto Tag",
        "Release - Version PR",
        # Aggregate failures need exact-head producer/attempt reconciliation
        # before recovery. Do not give this safety gate a new automatic retry
        # budget; an operator diagnoses and reruns proven infra loss manually.
        "PR - All Checks Gate",
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
    ``reusable-sbom.yml`` with ``create-attestation: true``, or a
    ``reusable-docker*`` workflow with ``cosign-sign: true``. For a reusable
    call the caller's list only matters under ``replace`` semantics.

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
            if (
                "reusable-sbom.yml" in uses
                and with_block.get("create-attestation") is True
            ):
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
                # The reusable mints the OIDC token inside its own job; the
                # docker-tools-* callers sign keyless under replace mode
                # without listing token.actions.githubusercontent.com and
                # release fine, so only the Sigstore hosts are required here.
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
                    # A direct attest or keyless cosign step fetches its own
                    # identity token, so the OIDC host is required too.
                    attests or signs,
                ),
            )
    return found


_ATTESTATION_STORE_HOST = "*.blob.core.windows.net:443"

_ATTESTATION_VERIFY_HOSTS = frozenset(
    {
        "api.github.com:443",
        "tuf-repo-cdn.sigstore.dev:443",
        _ATTESTATION_STORE_HOST,
    },
)


def _python_code(text: str) -> str:
    """Return a Python module's source without comments and docstrings.

    Other string literals stay, so a verifier call spelled as an argv list
    (``subprocess.run(["gh", "attestation", "verify", ...])``) still counts.

    Args:
        text: Python source.

    Returns:
        The source with comment tokens and docstring statements dropped, or
        an empty string when it does not parse.
    """
    try:
        module = ast.parse(text)
    except SyntaxError:
        return ""
    docstring_lines: set[int] = set()
    for node in ast.walk(module):
        if not isinstance(
            node,
            ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
        ):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            first = body[0]
            docstring_lines.update(
                range(first.lineno, (first.end_lineno or first.lineno) + 1),
            )
    lines = text.splitlines()
    try:
        comment_starts = {
            token.start[0]: token.start[1]
            for token in tokenize.generate_tokens(io.StringIO(text).readline)
            if token.type == tokenize.COMMENT
        }
    except tokenize.TokenError:
        comment_starts = {}
    return "\n".join(
        line[: comment_starts.get(number, len(line))]
        for number, line in enumerate(lines, start=1)
        if number not in docstring_lines
    )


def _code_lines(text: str, *, python: bool = False) -> str:
    """Return the executable part of a script or ``run:`` block.

    Args:
        text: Shell or YAML ``run:`` source, or a Python module.
        python: Treat ``text`` as Python (see :func:`_python_code`).

    Returns:
        The source without comment lines, heredoc bodies (shell) or
        docstrings (Python).
    """
    if python:
        return _python_code(text)
    kept: list[str] = []
    heredoc_end: str | None = None
    for line in text.splitlines():
        if heredoc_end is not None:
            # Inside a heredoc (usage text, templates): data, not code.
            if line.strip() == heredoc_end:
                heredoc_end = None
            continue
        if line.lstrip().startswith("#"):
            continue
        kept.append(line)
        opener = _HEREDOC_OPENER.search(line)
        if opener is not None:
            heredoc_end = opener.group("q") or opener.group("d") or opener.group("tag")
    return "\n".join(kept)


# ``<<`` or ``<<-`` with a quoted or bare delimiter. ``<<<`` is a here-string
# and ``$((1 << 2))`` a shift: a bare delimiter starts with a letter or
# underscore and stops at a shell operator, so ``<<EOF;`` yields ``EOF``.
_HEREDOC_OPENER = re.compile(
    r"""(?<!<)<<(?!<)-?\s*(?:'(?P<q>[^']+)'|"(?P<d>[^"]+)"|(?P<tag>[A-Za-z_][^\s'"<;&|)]*))""",
)


# ``gh attestation verify`` as a command: the ``gh`` binary or a variable
# holding it (``"$gh_cmd" attestation verify``), not prose quoting it.
_VERIFIER_CALL = re.compile(
    r"""(?:\bgh\b|gh_cmd"?\}?)["',\s]+attestation["',\s]+verify\b""",
)


def _attestation_verifying_scripts() -> set[str]:
    """Return the repo-relative paths of every script running the verifier.

    Any shell or Python script under ``scripts/`` whose code (comments and,
    for Python, string literals stripped) runs ``gh attestation verify``
    counts, and so does any script that invokes one of those scripts,
    transitively, so a wrapper cannot hide the call. Documentation files are
    not consulted.

    Returns:
        Repo-relative paths (``scripts/...``).
    """
    scripts_dir = _REPO_ROOT / "scripts"
    sources = {
        str(path.relative_to(_REPO_ROOT)): _code_lines(
            path.read_text(encoding="utf-8"),
            python=path.suffix == ".py",
        )
        for path in scripts_dir.rglob("*")
        if path.is_file() and path.suffix in {".sh", ".bash", ".py"}
    }
    verifying = {name for name, code in sources.items() if _VERIFIER_CALL.search(code)}
    while True:
        wrappers = {
            name
            for name, code in sources.items()
            if name not in verifying
            and any(_invokes(code=code, script=script) for script in verifying)
        }
        if not wrappers:
            return verifying
        verifying |= wrappers


def _invokes(*, code: str, script: str) -> bool:
    """Return whether ``code`` invokes ``script`` as a command or argument.

    Matches the script by its repo-relative path or by its basename as a
    whole path token (``bash verify_artifacts.sh``, ``./x/verify_artifacts.sh``,
    ``"$ROOT/scripts/ci/x.sh"``), but not a same-named file in another
    directory tree and not a bare mention inside a longer word.

    Args:
        code: Script or ``run:`` source with comments stripped.
        script: Repo-relative script path.

    Returns:
        ``True`` when the script is invoked.
    """
    name = Path(script).name
    parent = Path(script).parent.name
    pattern = re.compile(
        r"""(?:^|[\s"'=(])(?P<path>/?(?:[A-Za-z0-9_.${}-]+/)*"""
        + re.escape(name)
        + r""")(?=$|[\s"');&|])""",
        re.MULTILINE,
    )
    for match in pattern.finditer(code):
        path = match.group("path")
        if path == script or path.endswith(f"/{script}"):
            return True
        parts = path.split("/")
        if len(parts) == 1:
            return True
        if parts[-2] == parent and all(part in {".", ".."} for part in parts[:-2]):
            return True
    return False


def _attestation_verifying_jobs() -> list[tuple[str, str, set[str] | None]]:
    """Collect every job that runs ``gh attestation verify`` and its allowlist.

    A job verifies when the code of one of its ``run:`` steps (comments
    stripped) invokes the verifier directly or runs a repo script that does.
    Only jobs with their own harden-runner step are collected; reusable
    callers are covered by the reusable's own default allowlist unless they
    pass a replace-mode list.

    Returns:
        Tuples of (workflow file, job name, literal allowlist or None).
    """
    scripts = _attestation_verifying_scripts()
    found: list[tuple[str, str, set[str] | None]] = []
    for path in _workflow_paths():
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_name, job in (workflow.get("jobs") or {}).items():
            steps = job.get("steps") or []
            runs = [_code_lines(str(step.get("run", ""))) for step in steps]
            verifies = any(
                "attestation verify" in run
                or any(_invokes(code=run, script=script) for script in scripts)
                for run in runs
            )
            if not verifies:
                continue
            harden = [
                step
                for step in steps
                if str(step.get("uses", "")).startswith("step-security/harden-runner@")
            ]
            assert_that(harden).described_as(
                f"{path.name}:{job_name} verifies attestations but has no "
                "harden-runner step",
            ).is_length(1)
            found.append(
                (
                    path.name,
                    job_name,
                    _endpoint_set(
                        (harden[0].get("with") or {}).get("allowed-endpoints"),
                    ),
                ),
            )
    return found


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

#: The committed review-lanes manifest (ADR-0011 A, #2869): the only place
#: a dogfood provider host, credential binding or endpoint is defined.
_REVIEW_LANES_MANIFEST = _REPO_ROOT / "scripts" / "ci" / "ai_review_lanes.json"


#: The clause the dogfood review gates each credential on: membership of the
#: chain the secret-free lane job published (#2870). Tier 2 has no provider to
#: select, so it joins its own trusted-event gate to the same clause.
_CHAIN_MEMBERSHIP_RE = re.compile(
    r"contains\(fromJSON\(needs\.lane\.outputs\.chain\), '[a-z0-9-]+'\)",
)


#: The two routing inputs the resolver accepts, and the only step that may
#: read them: a selector can only name a declared row, so an inherited value
#: can at worst pick another declared lane, which the sticky prints.
_LANE_SELECTOR_VARS = {"vars.LINTRO_AI_PROVIDER", "vars.LINTRO_AI_MODEL"}

#: Every spelling of a ``vars`` read GitHub accepts: ``vars.NAME``,
#: ``vars['NAME']`` and ``vars["NAME"]``.
_VARS_READ_RE = re.compile(
    r"\bvars(?:\.(?P<dot>[A-Za-z_][A-Za-z0-9_]*)"
    r"|\[\s*['\"](?P<bracket>[A-Za-z_][A-Za-z0-9_]*)['\"]\s*\])",
)


def _vars_reads(blob: str) -> list[str]:
    """Return every variable name *blob* reads from ``vars``, in order.

    Args:
        blob: Workflow text or a joined set of expression values.

    Returns:
        The names, one per read, in any spelling GitHub accepts.
    """
    return [
        match.group("dot") or match.group("bracket")
        for match in _VARS_READ_RE.finditer(blob)
    ]


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
    # Total review spend across a PR's rounds (#2796); the smoke reviews no PR.
    "LINTRO_AI_REVIEW_PR_BUDGET_USD",
    # Review-state artifact upload (#2173); the smoke persists nothing.
    "ACTIONS_RUNTIME_TOKEN",
    "ACTIONS_RESULTS_URL",
    # The resolved chain and rows; Tier 2 drives every lane and binds none.
    "LINTRO_REVIEW_CHAIN",
    "LINTRO_REVIEW_LANES",
    # The codex session secret: Tier 2 restores it in its own step and the
    # runner mounts the directory; the smoke step never sees the secret.
    "CODEX_AUTH_JSON",
    # The gateway tokens travel under their secret names to the dogfood
    # wrapper, which binds the active lane's to ANTHROPIC_AUTH_TOKEN for the
    # attempt; Tier 2 binds them to ANTHROPIC_AUTH_TOKEN directly (asserted
    # below).
    "ZAI_AUTH_TOKEN",
    "KIMI_API_KEY",
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
    assert_that(harden["with"]["egress-policy"]).is_equal_to("audit")
    return set(str(harden["with"]["allowed-endpoints"]).split())


def _dogfood_provider_egress() -> set[str]:
    """Return every provider host any declared review lane can reach.

    Derived from the committed manifest, the only place a lane host is
    defined (ADR-0011 A), so a new lane cannot be declared without the Tier 2
    assertion below noticing.

    Returns:
        The union of every lane row's ``host:port`` endpoints.
    """
    manifest = json.loads(_REVIEW_LANES_MANIFEST.read_text(encoding="utf-8"))
    endpoints: set[str] = set()
    for row in manifest["lanes"].values():
        endpoints.update(row["egress"])
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
    """Rewrite a dogfood chain-gated expression into its Tier 2 equivalent.

    The dogfood review gates each credential on membership of the resolved
    chain (#2870): its wrapper binds one per attempt. Tier 2 has no wrapper,
    so the two anthropic credentials, which the claude CLI reads directly,
    key on the *active* lane instead; every other lane's credential is
    forwarded whenever its trusted-event gate allows. The secret reference
    must survive untouched.

    Args:
        expression: The normalised dogfood expression.

    Returns:
        The normalised expression Tier 2 must carry for the same variable.
    """
    match = _CHAIN_MEMBERSHIP_RE.search(expression)
    assert_that(match).described_as(expression).is_not_none()
    assert match is not None  # narrow for mypy
    gate = f"env.{_AI_CONTRACT_GATE_ENV} == 'true'"
    # The subscription token keys on the active lane, like the gateway
    # token and endpoint: a chain that lists the subscription as a fallback
    # behind the gateway must not hand the claude CLI both credentials.
    # (The gateway token is bound under another name; it is not mirrored
    # here.) Every other lane's credential is forwarded whenever the gate
    # allows, because Tier 2 drives every lane in one job.
    if "'anthropic'" in match.group(0):
        active = "needs.lane.outputs.lane == 'anthropic'"
        return expression.replace(match.group(0), f"({gate} && {active})", 1)
    return expression.replace(match.group(0), gate, 1)


# --- Release version-skew audit wiring (#1712) ------------------------------

_SKEW_WORKFLOW = "release-version-skew-audit.yml"

_SKEW_SCRIPT = "scripts/ci/check-release-version-skew.py"


_DOGFOOD_TOOL_OPTIONS_RE = re.compile(
    r"pydoclint:timeout=\d+,[^\s]+osv_scanner:check_suppressions=[^\s,]+",
)

_EXPECTED_DOGFOOD_TOOL_OPTIONS = (
    "pydoclint:timeout=120,black:timeout=120,bandit:timeout=120,prettier:timeout=120,"
    "mypy:timeout=120,gitleaks:timeout=120,typos:timeout=120,semgrep:timeout=600,"
    "osv_scanner:check_suppressions=false"
)


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
        The matrix include entries, one per active table row.

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


# The hosts ``scripts/utils/install-uv.sh`` downloads the uv release asset
# from. GitHub moved release assets from objects.githubusercontent.com to
# release-assets.githubusercontent.com; a job that bootstraps uv under an egress
# block needs both, and the smoke lost every provider row to the missing one
# before any smoke ran (#2741).
_UV_RELEASE_ASSET_HOSTS = frozenset(
    {
        "objects.githubusercontent.com:443",
        "release-assets.githubusercontent.com:443",
    },
)

_SETUP_ENV_ACTION = "./.github/actions/setup-env"


def _hardened_jobs_that_bootstrap_uv() -> dict[str, dict[str, Any]]:
    """Return every hardened job that installs uv through ``setup-env``.

    Returns:
        ``{"<workflow file>:<job id>": job}`` for each job whose steps use the
        local ``setup-env`` action under a harden-runner ``block`` or ``audit`` policy.
    """
    found: dict[str, dict[str, Any]] = {}
    for path in _workflow_paths():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for job_id, job in (data.get("jobs") or {}).items():
            steps = job.get("steps") or []
            if not any(step.get("uses") == _SETUP_ENV_ACTION for step in steps):
                continue
            harden = next(
                (
                    step
                    for step in steps
                    if str(step.get("uses", "")).startswith("step-security/")
                ),
                None,
            )
            if harden is None or harden["with"].get("egress-policy") not in {
                "block",
                "audit",
            }:
                continue
            found[f"{path.name}:{job_id}"] = job
    return found


#: The Actions results service, which upload-artifact and download-artifact
#: talk to. Under ``egress-policy: block`` a job that moves an artifact
#: without these on its allowlist fails on blocked egress, and the failure
#: reads as the artifact being absent rather than as a network block.
_ARTIFACT_ENDPOINTS: tuple[str, ...] = (
    "pipelines.actions.githubusercontent.com:443",
    "results-receiver.actions.githubusercontent.com:443",
)


_GHCR_CLEANUP_LANE_JOBS = (
    "prune-untagged",
    "sweep-ci-tags",
    "sweep-sha-tags",
    "sweep-tools-candidates",
    "prune-untagged-base",
)


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
    "reusable-release-failure-notifier.yml": frozenset({"max-reruns"}),
    "reusable-test-python.yml": frozenset({"coverage-threshold"}),
}

_LGTM_CI_NUMBER_INPUTS_COMMON: frozenset[str] = frozenset({"timeout-minutes"})

_BLOCK_SCALAR_WITH = re.compile(r"^      (?P<key>[a-z-]+):\s*[>|][-+]?\s*$")


_RELEASE_IMAGE_JOBS = ("docker-base", "docker-full", "docker-ai")


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
        "!cancelled() && "
        "needs.changes.result == 'success' && "
        "needs.semgrep-lock.result == 'success' && "
        "needs.code-quality-gate.result == 'success' && "
        "needs.security-audit.result == 'success' && "
        "needs.integration-test.result == 'success' && "
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


# --- #2562 PR (d): the release failure notifier ------------------------------

_NOTIFIER_REUSABLE = "reusable-release-failure-notifier.yml"


# --- #2633: validation channel switch and fault injection --------------------

#: The two repository variables the validation runbook flips
#: (docs/release-validation.md). Read once, by classify-tag, never elsewhere.
_VALIDATION_CHANNELS_VAR = "RELEASE_VALIDATION_CHANNELS"

_RELEASE_FAULT_VAR = "RELEASE_FAULT"

_RELEASE_FAULT_SCRIPT = "scripts/ci/release-fault.sh"

#: A ``vars.X`` read in a workflow expression (secrets are a separate concern).
_VARS_REFERENCE_RE = re.compile(r"\bvars\.[A-Za-z_][A-Za-z0-9_]*")

_ACTIONS_V_GUARD = "!startsWith(github.ref_name, 'actions-v')"

#: Every workflow the tag pipeline calls locally; none may read vars.*.
_TAG_PIPELINE_CALLEES = (
    "publish-npm.yml",
    "build-binaries.yml",
    "publish-binaries.yml",
    "docker-build-publish.yml",
    "mirror-release.yml",
)


def _evaluate_tag_gate(
    condition: str,
    *,
    is_prerelease: str,
    validation_channels: str,
) -> bool:
    """Evaluate a tag-pipeline job gate for a classify-tag output pair.

    The ``actions-v`` recursion guard is true for every release tag; the
    mirror token probe is treated as present so only the classifier outputs
    decide.

    Args:
        condition: The job's raw ``if:`` expression.
        is_prerelease: Simulated ``classify-tag.outputs.is_prerelease``.
        validation_channels: Simulated ``classify-tag.outputs.validation_channels``.

    Returns:
        Whether the job would run.
    """
    expr = _normalize_github_expr(condition)
    expr = expr.replace("${{", "").replace("}}", "")
    expr = _replace_github_token(expr, token=_ACTIONS_V_GUARD, replacement="True")
    return _evaluate_github_if(
        expr,
        cancelled=False,
        results={},
        outputs={
            "classify-tag": {
                "is_prerelease": is_prerelease,
                "validation_channels": validation_channels,
            },
            # A job output, not a credential.
            "mirror-token": {"has_token": "true"},  # nosec B105
        },
    )


def _run_classifier(*, tag: str, env: dict[str, str]) -> dict[str, str]:
    """Run classify-release-tag.py as the job does and parse its outputs.

    Args:
        tag: The tag to classify.
        env: The switch variables to set (everything else is scrubbed).

    Returns:
        Output name to value.
    """
    scrubbed = {
        key: value
        for key, value in os.environ.items()
        if key not in (_VALIDATION_CHANNELS_VAR, _RELEASE_FAULT_VAR, "GITHUB_OUTPUT")
    }
    with tempfile.TemporaryDirectory() as tmp:
        github_output = Path(tmp) / "gh_output"
        result = subprocess.run(  # nosec B603 - fixed argv against a repo script
            [
                sys.executable,
                str(_REPO_ROOT / "scripts" / "ci" / "classify-release-tag.py"),
                tag,
            ],
            capture_output=True,
            text=True,
            check=True,
            env={**scrubbed, **env, "GITHUB_OUTPUT": str(github_output)},
        )
        # stdout stays the one-line contract the mirror resolver captures;
        # the job reads everything from GITHUB_OUTPUT (delimiter form).
        assert_that(result.stdout.strip()).matches(r"^is_prerelease=(true|false)$")
        lines = github_output.read_text(encoding="utf-8").splitlines()
    outputs: dict[str, str] = {}
    for index in range(0, len(lines), 3):
        name, delimiter = lines[index].split("<<", 1)
        assert_that(lines[index + 2]).is_equal_to(delimiter)
        outputs[name] = lines[index + 1]
    return outputs


def _run_fault_step(
    *,
    fault: str,
    release_fault: str,
) -> subprocess.CompletedProcess[str]:
    """Run release-fault.sh the way the fault step does.

    Args:
        fault: The step's own fault name (the script argument).
        release_fault: The value of classify-tag's ``release_fault`` output.

    Returns:
        The completed process.
    """
    return subprocess.run(  # nosec B603 - fixed argv against a repo script
        [str(_REPO_ROOT / _RELEASE_FAULT_SCRIPT), fault],
        capture_output=True,
        text=True,
        check=False,
        env={
            **{k: v for k, v in os.environ.items() if k != "GITHUB_STEP_SUMMARY"},
            "RELEASE_FAULT": release_fault,
        },
    )
