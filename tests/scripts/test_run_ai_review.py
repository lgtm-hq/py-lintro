"""Tests for the dogfood AI review CI helpers.

Covers the graceful-skip behaviour of ``run-ai-review.sh``, the review CLI
flags the script relies on, and that the ``ai-review.yml`` workflow parses as
valid YAML and feeds ``LINTRO_AI_*`` from repo Actions variables (#1971).
"""

from __future__ import annotations

import math
import os
import re
import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from pathlib import Path
from typing import Any

import pytest
import yaml
from assertpy import assert_that
from click.testing import CliRunner

from lintro.ai import transport
from lintro.cli import cli

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_SCRIPT = REPO_ROOT / "scripts" / "ci" / "enable_review_config.py"
SHELL_SCRIPT = REPO_ROOT / "scripts" / "ci" / "run-ai-review.sh"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ai-review.yml"
PROJECT_CONFIG = REPO_ROOT / ".lintro-config.yaml"

#: Guarded provider credentials. Anthropic dogfood uses the ``claude`` CLI
#: OAuth token; Cursor dogfood uses ``CURSOR_API_KEY``.
CREDENTIAL_ENV = "CLAUDE_CODE_OAUTH_TOKEN"
CURSOR_CREDENTIAL_ENV = "CURSOR_API_KEY"
PROVIDER_CREDENTIAL_ENVS = (CREDENTIAL_ENV, CURSOR_CREDENTIAL_ENV)
_PINNED_CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
_HEAD_REF_RE = re.compile(
    r"github\.event\.pull_request\.head\.(?:sha|ref|name)\b"
    r"|github\.(?:head_ref|sha|ref_name|ref)\b",
)
_GIT_HEAD_FETCH_RE = re.compile(
    r"\bgit(?:\s+\S+)*\s+(?:fetch|pull|checkout)\b",
)
_GH_PR_CHECKOUT_RE = re.compile(
    r"\bgh(?:\s+\S+)*\s+pr(?:\s+\S+)*\s+checkout\b",
)
_PULL_HEAD_REF_RE = re.compile(r"pull/\S*/head")
_CHECKOUT_LIKE_RE = re.compile(r"checkout")
_CURSOR_EGRESS_HOSTS = (
    "downloads.cursor.com:443",
    "api2.cursor.sh:443",
    "api3.cursor.sh:443",
    "agentn.global.api5.cursor.sh:443",
    "repo42.cursor.sh:443",
)


def _executable_run_text(run_block: str) -> str:
    """Return a ``run:`` block with full-line shell comments removed.

    Args:
        run_block: Raw workflow ``run`` string.

    Returns:
        Executable lines only, joined with newlines.
    """
    without_comments = "\n".join(
        line for line in run_block.splitlines() if not line.lstrip().startswith("#")
    )
    return without_comments.replace("\\\n", " ")


def _is_checkout_like_action(uses: str) -> bool:
    """Return whether a ``uses:`` value is a checkout-like action.

    Args:
        uses: Workflow ``uses`` pin (``owner/repo@sha``).

    Returns:
        True when the action repo looks like a checkout action.
    """
    name = uses.split("@", 1)[0].split("#", 1)[0].strip().lower()
    repo = name.rsplit("/", 1)[-1]
    return bool(_CHECKOUT_LIKE_RE.search(f"/{repo}"))


def _run_shell(
    *,
    args: list[str],
    env_overrides: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    """Run the shell helper with a controlled environment.

    Args:
        args: Positional arguments passed to the script.
        env_overrides: Environment variables layered onto a minimal base.

    Returns:
        The completed subprocess result.
    """
    env = {"PATH": os.environ.get("PATH", "")}
    env.update(env_overrides)
    return subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
        [str(SHELL_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_patch_script_is_gone() -> None:
    """The YAML-patching workaround must not return (#1971)."""
    assert_that(CONFIG_SCRIPT.exists()).is_false()
    shell_text = SHELL_SCRIPT.read_text(encoding="utf-8")
    assert_that(shell_text).does_not_contain("enable_review_config.py")


def test_committed_config_keeps_ai_off_with_review_ready() -> None:
    """Local default stays AI-off; CI turns it on via ``LINTRO_AI_ENABLED=1``.

    ``ai.review: true`` is committed so enabling the master switch does not
    rely on the deprecated implied-sub-toggle path. ``ai.max_cost_usd`` is
    unset after the #2156 rollout (no committed cap; CI forwards
    ``LINTRO_AI_MAX_COST_USD``, and dogfood operators set ``uncapped``).
    Historical: #2018 / 9f43a98a briefly shipped 0.50; #2025 restored 2.00 as
    the interim committed cap.
    """
    loaded = yaml.safe_load(PROJECT_CONFIG.read_text(encoding="utf-8"))
    ai_section = loaded["ai"]
    assert_that(ai_section["enabled"]).is_false()
    assert_that(ai_section["review"]).is_true()
    assert_that(ai_section).does_not_contain_key("max_cost_usd")
    workflow_text = WORKFLOW.read_text(encoding="utf-8")
    assert_that(workflow_text).contains("#2018 / 9f43a98a")
    assert_that(workflow_text).contains("LINTRO_AI_MAX_COST_USD")
    assert_that(workflow_text).does_not_contain("|| '2.00'")
    assert_that(workflow_text).does_not_contain("0.50 that landed with\n# #1971")


def test_workflow_feeds_lintro_ai_env_from_repo_variables() -> None:
    """The review step overlays AI settings from Actions variables."""
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = loaded["jobs"]["ai-review"]["steps"]
    review_steps = [
        step for step in steps if str(step.get("name", "")).startswith("Run AI review")
    ]
    assert_that(review_steps).is_length(1)
    env = review_steps[0]["env"]
    assert_that(env["LINTRO_AI_ENABLED"]).is_equal_to("1")
    assert_that(env["LINTRO_AI_TRANSPORT"]).is_equal_to(
        "${{ vars.LINTRO_AI_TRANSPORT || 'cli' }}",
    )
    assert_that(env["LINTRO_AI_PROVIDER"]).is_equal_to(
        "${{ vars.LINTRO_AI_PROVIDER || 'anthropic' }}",
    )
    assert_that(env["LINTRO_AI_MODEL"]).is_equal_to("${{ vars.LINTRO_AI_MODEL }}")
    assert_that(env["LINTRO_AI_MAX_COST_USD"]).is_equal_to(
        "${{ vars.LINTRO_AI_MAX_COST_USD }}",
    )
    assert_that(env).does_not_contain_key("AI_REVIEW_MAX_COST_USD")


def test_shell_help_exits_zero() -> None:
    """The --help flag prints usage and exits 0."""
    result = _run_shell(args=["--help"], env_overrides={})

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Usage:")
    assert_that(result.stdout).contains("--locate-prior-state")


def test_shell_locate_prior_state_is_a_noop_without_github() -> None:
    """Locate mode does not require a provider credential and always exits 0."""
    result = _run_shell(args=["--locate-prior-state"], env_overrides={})

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("run-id=")


def test_shell_fails_visibly_without_oauth_token() -> None:
    """A missing credential is a visible failure, never a silent green pass.

    This is the #1826 regression guard: the script used to exit 0 here, so a PR
    whose review never ran still showed ``AI Review ✓``. The credential checked
    is the CLI transport's OAuth token (#1894), not an API key.
    """
    result = _run_shell(
        args=[],
        env_overrides={CREDENTIAL_ENV: "", "PR_NUMBER": "123"},
    )

    assert_that(result.returncode).is_equal_to(1)
    assert_that(result.stdout).contains("::error")
    assert_that(result.stdout).contains("no provider credential")
    assert_that(result.stderr).contains("nothing was reviewed")


def test_shell_fails_visibly_without_cursor_key_when_provider_is_cursor() -> None:
    """Cursor overlay must not treat a Claude token as the Cursor credential.

    ``LINTRO_AI_PROVIDER=cursor`` with only ``CLAUDE_CODE_OAUTH_TOKEN`` set is
    how #2018's first dogfood run looked after the Actions variables flipped:
    the wrapper would have proceeded, then crashed inside ``get_provider``.
    """
    result = _run_shell(
        args=[],
        env_overrides={
            CREDENTIAL_ENV: "dummy-claude-token",
            CURSOR_CREDENTIAL_ENV: "",
            "LINTRO_AI_PROVIDER": "cursor",
            "PR_NUMBER": "123",
        },
    )

    assert_that(result.returncode).is_equal_to(1)
    assert_that(result.stdout).contains("::error")
    assert_that(result.stdout).contains("no provider credential")


def test_shell_lowercases_provider_without_bash4_syntax() -> None:
    """Provider matching must work on bash 3.2 (no ``${var,,}``)."""
    shell_text = SHELL_SCRIPT.read_text(encoding="utf-8")
    assert_that(shell_text).does_not_contain("${provider,,}")
    assert_that(shell_text).contains("tr '[:upper:]' '[:lower:]'")

    result = _run_shell(
        args=[],
        env_overrides={
            CREDENTIAL_ENV: "dummy-claude-token",
            CURSOR_CREDENTIAL_ENV: "",
            "LINTRO_AI_PROVIDER": "CURSOR",
            "PR_NUMBER": "123",
        },
    )

    assert_that(result.returncode).is_equal_to(1)
    assert_that(result.stdout).contains("no provider credential")


def test_shell_no_credential_path_respects_transport_overlay() -> None:
    """A missing credential must classify against ``LINTRO_AI_TRANSPORT``.

    Hardcoding ``--transport cli`` on the no-credential path mislabels an
    ``api`` overlay as a CLI OAuth failure (#2025).
    """
    result = _run_shell(
        args=[],
        env_overrides={
            CREDENTIAL_ENV: "",
            "LINTRO_AI_TRANSPORT": "API",
            "PR_NUMBER": "123",
        },
    )

    assert_that(result.returncode).is_equal_to(1)
    assert_that(result.stdout).contains("[api]")
    assert_that(result.stdout).contains("no provider credential")
    assert_that(result.stdout).does_not_contain("[cli]")


def test_shell_accepts_cursor_key_without_claude_token() -> None:
    """A Cursor key satisfies the guard even when the Claude token is absent.

    The failure here must be the missing PR number (classifier, invoked), not
    a missing-credential skip — otherwise flipping the provider variable would
    still demand the Anthropic secret.
    """
    result = _run_shell(
        args=[],
        env_overrides={
            CREDENTIAL_ENV: "",
            CURSOR_CREDENTIAL_ENV: "dummy-cursor-key",
            "LINTRO_AI_PROVIDER": "cursor",
            "PR_NUMBER": "",
        },
    )

    assert_that(result.returncode).is_equal_to(1)
    assert_that(result.stdout).contains("never invoked")
    assert_that(result.stdout).contains("No PR number provided")
    assert_that(result.stdout).does_not_contain("no provider credential")


def test_shell_fails_visibly_without_pr_number() -> None:
    """A configured credential but no PR number fails *through the classifier*.

    Exiting directly would redden the check without emitting an annotation or a
    summary, which is a red check that cannot explain itself.
    """
    result = _run_shell(
        args=[],
        env_overrides={CREDENTIAL_ENV: "dummy-token", "PR_NUMBER": ""},
    )

    assert_that(result.returncode).is_equal_to(1)
    assert_that(result.stdout).contains("::error")
    assert_that(result.stdout).contains("never invoked")
    assert_that(result.stdout).contains("No PR number provided")


def test_shell_writes_outcome_to_step_summary(tmp_path: Path) -> None:
    """The outcome is appended to the job summary so the PR list is readable.

    Args:
        tmp_path: Temporary directory holding the fake step-summary file.
    """
    summary = tmp_path / "summary.md"
    result = _run_shell(
        args=[],
        env_overrides={
            CREDENTIAL_ENV: "",
            "PR_NUMBER": "123",
            "GITHUB_STEP_SUMMARY": str(summary),
        },
    )

    assert_that(result.returncode).is_equal_to(1)
    written = summary.read_text(encoding="utf-8")
    assert_that(written).contains("AI Review")
    assert_that(written).contains("no provider credential")


def test_review_cli_accepts_script_flags() -> None:
    """The review command exposes the flags the script invokes."""
    runner = CliRunner()
    result = runner.invoke(cli, ["review", "--help"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("--pr")
    assert_that(result.output).contains("--depth")
    assert_that(result.output).contains("--output")
    assert_that(result.output).contains("json")


def test_workflow_yaml_parses() -> None:
    """The ai-review workflow is valid YAML with the expected trigger."""
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    assert_that(loaded).contains_key("jobs")
    assert_that(loaded["jobs"]).contains_key("ai-review")
    trigger = loaded[True] if True in loaded else loaded["on"]
    assert_that(trigger).contains_key("pull_request_target")
    assert_that(trigger).does_not_contain_key("pull_request")


def test_workflow_concurrency_keys_on_the_pr_number() -> None:
    """``pull_request_target`` sets ``github.ref`` to the base branch.

    Keying concurrency on ``github.ref`` would cancel every in-flight review
    whenever any other PR targeting that branch synchronized.
    """
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    concurrency = loaded["concurrency"]
    assert_that(concurrency["group"]).is_equal_to(
        "ai-review-${{ github.event.pull_request.number || github.ref }}",
    )
    assert_that(concurrency["cancel-in-progress"]).is_true()


def test_workflow_runs_on_every_pr_without_a_paths_filter() -> None:
    """The pull_request_target trigger carries no ``paths`` filter (#1902).

    The old ``lintro/**`` filter meant CI, script, and workflow PRs shipped with
    no AI review at all — the #1900 timeout bug went out exactly that way. Under
    subscription billing every PR gets the dogfood review.
    """
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    trigger = loaded[True] if True in loaded else loaded["on"]
    pull_request = trigger["pull_request_target"]
    assert_that(pull_request).does_not_contain_key("paths")
    assert_that(pull_request).does_not_contain_key("paths-ignore")


_BEST_EFFORT_STATE_STEPS = frozenset(
    {
        "Locate prior review-state run",
        "Download prior review-state artifacts",
    },
)


def test_workflow_never_rewrites_its_conclusion_to_success() -> None:
    """The review step cannot swallow failure; locate/download may.

    Job-level ``continue-on-error`` rewrites the job conclusion to ``success``,
    which is exactly how a review that produced nothing kept reading as a pass for
    months (#1826). Locate and download are best-effort plumbing: their
    ``continue-on-error`` keeps a missing prior artifact from skipping the
    review. The review step itself still has none. The check is not required,
    so a red conclusion is visible without being blocking.
    """
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    job = loaded["jobs"]["ai-review"]
    assert_that(job).does_not_contain_key("continue-on-error")
    review_seen = False
    for step in job["steps"]:
        name = str(step.get("name", ""))
        if name in _BEST_EFFORT_STATE_STEPS:
            assert_that(step.get("continue-on-error")).described_as(
                f"step {name!r} is best-effort plumbing",
            ).is_true()
            continue
        if name.startswith("Run AI review"):
            review_seen = True
        assert_that(step).described_as(
            f"step {name!r} must not swallow its own failure",
        ).does_not_contain_key("continue-on-error")
    assert_that(review_seen).is_true()


def test_workflow_job_is_same_repo_only() -> None:
    """The keyed job only runs for same-repository (non-fork) PRs.

    The job ``if`` guard combines the draft check with a head-repo equality
    check so fork PRs never attempt the job that has the secret in scope.
    """
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    guard = loaded["jobs"]["ai-review"]["if"]
    assert_that(guard).contains("github.event.pull_request.draft == false")
    assert_that(guard).contains(
        "github.event.pull_request.head.repo.full_name == github.repository",
    )


def test_workflow_job_reads_pull_requests() -> None:
    """The workflow token stays read-only: contents, PRs, and actions.

    ``actions/checkout`` reads the trusted base ref (``contents: read``).
    ``gh`` fetches the PR diff (``pull-requests: read``). Cross-run
    artifact listing needs ``actions: read`` (#2158). ``--post`` writes
    as ``lintro-review[bot]`` via the App token (#2050), so the
    job-scoped ``GITHUB_TOKEN`` never gains write.
    """
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    perms = loaded["jobs"]["ai-review"]["permissions"]
    assert_that(perms["pull-requests"]).is_equal_to("read")
    assert_that(perms["contents"]).is_equal_to("read")
    assert_that(perms["actions"]).is_equal_to("read")


def test_workflow_installs_from_base_ref_not_pr_head() -> None:
    """Lintro is installed from the trusted base ref, never the PR head.

    The checkout used for the keyed install must pin ``ref`` to the PR's base
    SHA so PR-controlled code never executes with the provider credential in
    scope. The PR itself is still reviewed via ``gh`` (diff fetched over the
    API), independent of the checked-out tree.

    Strict trust boundary (#2025): this job may have exactly one
    ``actions/checkout``. A second checkout with any ``path:`` (the #2018
    ``.ai-review-installer`` side-checkout of ``github.sha``) executes
    PR-authored code on the credential-holding job. Two-PR bootstrap:
    new CI scripts land inert in PR 1; the workflow step that invokes them
    lands in PR 2 after PR 1 is on main. Never land a new script and its
    first workflow invocation in the same PR.
    """
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    steps = loaded["jobs"]["ai-review"]["steps"]
    checkout_steps = [
        step
        for step in steps
        if isinstance(step.get("uses"), str)
        and step["uses"].startswith("actions/checkout@")
    ]
    assert_that(checkout_steps).is_length(1)

    checkout = checkout_steps[0]
    assert_that(checkout).contains_key("with")
    assert_that(checkout["with"]).does_not_contain_key("path")
    # Structurally assert the checkout pins to the trusted base ref. A harmless
    # head-ref mention in a comment/log elsewhere in the file must not false-fail
    # this, so we assert on the parsed step rather than banning text file-wide.
    assert_that(checkout["with"]["ref"]).is_equal_to(
        "${{ github.event.pull_request.base.sha }}",
    )
    workflow_text = WORKFLOW.read_text(encoding="utf-8")
    assert_that(workflow_text).contains("TWO-PR BOOTSTRAP")
    assert_that(workflow_text).contains("default branch")
    assert_that(workflow_text).does_not_contain(
        "GitHub runs the workflow YAML from the PR",
    )
    assert_that(workflow_text).does_not_contain(".ai-review-installer")
    assert_that(workflow_text).does_not_contain("enable_cursor_workspace_trust")


@pytest.mark.parametrize(
    ("uses", "expected"),
    [
        (_PINNED_CHECKOUT, True),
        ("evil/checkout@deadbeef", True),
        ("acme/checkout-action@1", True),
        ("acme/pr-checkout@1", True),
        ("acme/pr-checkout-action@1", True),
        ("actions/setup-node@820762786026740c76f36085b0efc47a31fe5020", False),
        ("astral-sh/setup-uv@20cfd1bf945f4377ade1205e4dbc17946fc9a30d", False),
    ],
    ids=[
        "pinned-actions-checkout",
        "forked-checkout",
        "checkout-prefix",
        "checkout-suffix",
        "checkout-infix",
        "setup-node",
        "setup-uv",
    ],
)
def test_is_checkout_like_action(*, uses: str, expected: bool) -> None:
    """Checkout-like ``uses:`` values are the ones the audit must pin.

    Args:
        uses: A workflow ``uses`` pin.
        expected: Whether the pin is checkout-like.
    """
    assert_that(_is_checkout_like_action(uses)).is_equal_to(expected)


_TRUSTED_CHECKOUT_REF = "${{ github.event.pull_request.base.sha }}"


@pytest.mark.parametrize(
    ("ref", "trusted"),
    [
        (_TRUSTED_CHECKOUT_REF, True),
        ("", False),
        ("${{ github.head_ref }}", False),
        ("${{ github.ref }}", False),
        ("${{ github.ref_name }}", False),
        ("${{ github.sha }}", False),
        ("${{ github.event.pull_request.head.sha }}", False),
    ],
    ids=[
        "base-sha",
        "omitted-ref",
        "head-ref",
        "github-ref",
        "ref-name",
        "github-sha",
        "pr-head-sha",
    ],
)
def test_checkout_ref_must_be_base_sha(*, ref: str, trusted: bool) -> None:
    """Only the PR base SHA is a trusted checkout ref for this job.

    Args:
        ref: A checkout ``with.ref`` value, or empty when omitted.
        trusted: Whether the audit must accept the ref.
    """
    assert_that(ref == _TRUSTED_CHECKOUT_REF).is_equal_to(trusted)
    if ref:
        banned = any(
            marker in ref
            for marker in (
                "pull_request.head",
                "github.sha",
                "github.head_ref",
                "github.ref",
            )
        )
        assert_that(banned).is_equal_to(not trusted)


@pytest.mark.parametrize(
    ("run_block", "banned"),
    [
        ("gh pr checkout 12\n", True),
        ("gh --repo lgtm-hq/py-lintro pr checkout 12\n", True),
        ("gh pr --repo lgtm-hq/py-lintro checkout 12\n", True),
        ("gh pr \\\n  checkout 12\n", True),
        ("# decoy \\\ngh pr checkout 12\n", True),
        ("git fetch origin pull/12/head\n", True),
        ("git -C . checkout ${{ github.head_ref }}\n", True),
        ("git --git-dir=.git fetch origin pull/12/head\n", True),
        ("git pull origin ${{ github.event.pull_request.head.sha }}\n", True),
        (
            "# gh pr checkout is banned in executable steps\npython3 scripts/ci/ai_tools_arg_pin.py\n",
            False,
        ),
        ("python3 scripts/ci/ai_tools_arg_pin.py NODE_VERSION\n", False),
    ],
    ids=[
        "gh-pr-checkout",
        "gh-repo-pr-checkout",
        "gh-pr-repo-checkout",
        "gh-pr-checkout-continued",
        "comment-backslash-then-checkout",
        "git-fetch-pull-head",
        "git-dash-c-checkout-head-ref",
        "git-git-dir-fetch-pull-head",
        "git-pull-head-sha",
        "comment-only-mention",
        "pin-script",
    ],
)
def test_head_ref_fetch_patterns(*, run_block: str, banned: bool) -> None:
    """Banned head-ref fetch shapes are detected in executable ``run:`` text.

    Args:
        run_block: A sample workflow ``run`` block.
        banned: Whether the block must fail the audit.
    """
    executable = _executable_run_text(run_block)
    matched = any(
        pattern.search(executable)
        for pattern in (
            _GH_PR_CHECKOUT_RE,
            _GIT_HEAD_FETCH_RE,
            _PULL_HEAD_REF_RE,
            _HEAD_REF_RE,
        )
    )
    assert_that(matched).is_equal_to(banned)


def test_workflow_forbids_head_ref_fetches() -> None:
    """Head-ref fetches and checkout forks fail the structural audit (#2047).

    The existing one-checkout assertion cannot see ``gh pr checkout``,
    ``git fetch origin pull/N/head``, ``github.event.pull_request.head.sha``
    as a checkout ref, or a forked checkout action. Those all execute
    PR-authored code on the credential-holding job. Comments and the
    same-repo ``head.repo.full_name`` guard are not fetches.
    """
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = loaded["jobs"]["ai-review"]["steps"]

    checkout_uses: list[str] = []
    for step in steps:
        uses = step.get("uses")
        if isinstance(uses, str) and _is_checkout_like_action(uses):
            pin = uses.split("#", 1)[0].strip()
            checkout_uses.append(pin)
            assert_that(pin).is_equal_to(_PINNED_CHECKOUT)

        with_block = step.get("with") or {}
        ref = str(with_block.get("ref", ""))
        if isinstance(uses, str) and _is_checkout_like_action(uses):
            assert_that(ref).is_equal_to(
                "${{ github.event.pull_request.base.sha }}",
            )
        assert_that(ref).does_not_contain("pull_request.head")
        assert_that(ref).does_not_contain("github.sha")
        assert_that(ref).does_not_contain("github.head_ref")
        assert_that(ref).does_not_contain("github.ref")
        assert_that(_PULL_HEAD_REF_RE.search(ref)).is_none()

        run = step.get("run")
        if not isinstance(run, str):
            continue
        executable = _executable_run_text(run)
        assert_that(_GH_PR_CHECKOUT_RE.search(executable)).described_as(
            f"step {step.get('name')!r} must not run gh pr checkout",
        ).is_none()
        assert_that(_GIT_HEAD_FETCH_RE.search(executable)).described_as(
            f"step {step.get('name')!r} must not git fetch/pull/checkout",
        ).is_none()
        assert_that(_PULL_HEAD_REF_RE.search(executable)).described_as(
            f"step {step.get('name')!r} must not fetch pull/*/head",
        ).is_none()
        assert_that(_HEAD_REF_RE.search(executable)).described_as(
            f"step {step.get('name')!r} must not use pull_request.head sha/ref",
        ).is_none()

    assert_that(checkout_uses).is_equal_to([_PINNED_CHECKOUT])


def test_workflow_does_not_patch_cursor_workspace_trust() -> None:
    """#2023 defaults ``ai.cursor_trust_workspace``; the CI patcher is gone."""
    assert_that(
        (REPO_ROOT / "scripts" / "ci" / "enable_cursor_workspace_trust.py").exists(),
    ).is_false()
    names = [
        str(step.get("name", ""))
        for step in yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"][
            "ai-review"
        ]["steps"]
    ]
    assert_that(names).does_not_contain("Enable Cursor workspace trust")
    assert_that(names).does_not_contain(
        "Checkout cursor-agent installer (matches this workflow)",
    )


def test_workflow_secret_scoped_to_review_step_only() -> None:
    """Provider credentials are injected only into the review step env.

    Secrets must not appear in workflow- or job-level env maps, nor in
    earlier steps (checkout, CLI install, uv sync, etc.), so PR-controlled code
    paths never receive a token before the trusted base-ref install completes.
    This is the ordering control audited in #1317.
    """
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    workflow_env = loaded.get("env")
    job_env = loaded["jobs"]["ai-review"].get("env")
    for credential_env in PROVIDER_CREDENTIAL_ENVS:
        if workflow_env is not None:
            assert_that(workflow_env).does_not_contain_key(credential_env)
        if job_env is not None:
            assert_that(job_env).does_not_contain_key(credential_env)

    steps = loaded["jobs"]["ai-review"]["steps"]
    review_steps = [
        step for step in steps if str(step.get("name", "")).startswith("Run AI review")
    ]
    assert_that(review_steps).is_length(1)

    # Assert on the parsed env map, not on a dump of the whole step: a mention
    # in a `run:` line or a comment would satisfy a text search while the step
    # never actually received the secret.
    review_step = review_steps[0]
    assert_that(review_step["env"][CREDENTIAL_ENV]).is_equal_to(
        "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}",
    )
    assert_that(review_step["env"][CURSOR_CREDENTIAL_ENV]).is_equal_to(
        "${{ secrets.CURSOR_API_KEY }}",
    )
    for step in steps:
        if step is review_step:
            continue
        step_env = step.get("env") or {}
        for credential_env in PROVIDER_CREDENTIAL_ENVS:
            assert_that(step_env).described_as(
                f"step {step.get('name')!r}",
            ).does_not_contain_key(credential_env)


def test_workflow_reviews_pr_via_gh_not_working_tree() -> None:
    """The executable review command targets the PR and emits JSON.

    ``lintro review --pr`` collects the PR diff through the GitHub API, so the
    PR's changes are reviewed as data even though the checked-out tree is the
    base ref. Assert on the actual executable invocation line (skipping comment
    lines) so a stray comment mention can never satisfy the check on its own.
    """
    lines = SHELL_SCRIPT.read_text(encoding="utf-8").splitlines()
    command_lines = [
        line
        for line in lines
        if "uv run lintro review" in line and not line.lstrip().startswith("#")
    ]
    assert_that(command_lines).is_length(1)

    command = command_lines[0]
    assert_that(command).contains("--pr")
    assert_that(command).contains("--depth 1")
    assert_that(command).contains("--output json")
    # --post publishes the sticky review comment (and inline findings) on the PR.
    assert_that(command).contains("--post")


@pytest.mark.parametrize(
    "action_ref",
    [
        "step-security/harden-runner@05e31511f85b41b11d1cf0ef85d0992719546e2c",
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "astral-sh/setup-uv@20cfd1bf945f4377ade1205e4dbc17946fc9a30d",
        "actions/setup-node@820762786026740c76f36085b0efc47a31fe5020",
        "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1",
        "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
        "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
    ],
)
def test_workflow_pins_actions_to_sha(*, action_ref: str) -> None:
    """Third-party actions are pinned to full commit SHAs.

    Args:
        action_ref: The ``owner/repo@sha`` reference expected in the workflow.
    """
    content = WORKFLOW.read_text(encoding="utf-8")

    assert_that(content).contains(action_ref)


def test_workflow_mints_lintro_review_app_token_for_posting() -> None:
    """The App token is minted repo-scoped and used only for ``--post``.

    ``gh`` still fetches the PR diff with the workflow ``GITHUB_TOKEN``. The
    mint step must not pass ``owner:`` (that would mint an org-wide token)
    and must not be a second checkout.
    """
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = loaded["jobs"]["ai-review"]["steps"]

    mint_steps = [
        step
        for step in steps
        if str(step.get("uses", "")).startswith("actions/create-github-app-token@")
    ]
    assert_that(mint_steps).is_length(1)
    mint = mint_steps[0]
    assert_that(mint["id"]).is_equal_to("lintro-review-app")
    assert_that(mint["uses"]).is_equal_to(
        "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1",
    )
    mint_with = mint["with"]
    assert_that(mint_with["app-id"]).is_equal_to(
        "${{ secrets.LINTRO_REVIEW_APP_ID }}",
    )
    assert_that(mint_with["private-key"]).is_equal_to(
        "${{ secrets.LINTRO_REVIEW_APP_PRIVATE_KEY }}",
    )
    assert_that(mint_with["permission-issues"]).is_equal_to("write")
    assert_that(mint_with["permission-pull-requests"]).is_equal_to("write")
    assert_that(mint_with).does_not_contain_key("permission-contents")
    assert_that(mint_with).does_not_contain_key("owner")
    assert_that(mint_with).does_not_contain_key("repositories")
    assert_that(_is_checkout_like_action(mint["uses"])).is_false()

    review_steps = [
        step for step in steps if str(step.get("name", "")).startswith("Run AI review")
    ]
    assert_that(review_steps).is_length(1)
    review_env = review_steps[0]["env"]
    assert_that(review_env["GITHUB_TOKEN"]).is_equal_to(
        "${{ steps.lintro-review-app.outputs.token }}",
    )
    assert_that(review_env["GH_TOKEN"]).is_equal_to("${{ secrets.GITHUB_TOKEN }}")

    mint_index = steps.index(mint)
    review_index = steps.index(review_steps[0])
    assert_that(review_index).is_equal_to(mint_index + 1)

    app_secret_values = (
        "${{ secrets.LINTRO_REVIEW_APP_ID }}",
        "${{ secrets.LINTRO_REVIEW_APP_PRIVATE_KEY }}",
        "${{ steps.lintro-review-app.outputs.token }}",
    )
    for step in steps:
        if step is mint or step is review_steps[0]:
            continue
        step_env = step.get("env") or {}
        step_with = step.get("with") or {}
        blob = " ".join(
            str(value) for value in (*step_env.values(), *step_with.values())
        )
        for secret_value in app_secret_values:
            assert_that(blob).described_as(
                f"step {step.get('name')!r}",
            ).does_not_contain(
                secret_value,
            )


def test_workflow_never_puts_an_api_key_in_scope() -> None:
    """ANTHROPIC_API_KEY appears nowhere in the review workflow.

    Two reasons, both load-bearing. Its account balance is depleted, so an API
    key in scope buys nothing; and lintro's bare-mode detection treats a
    reachable ``ANTHROPIC_API_KEY`` as proof that a bare ``claude`` invocation
    can authenticate, which is exactly the wrong conclusion here (#1838/#1894).
    """
    content = WORKFLOW.read_text(encoding="utf-8")
    loaded = yaml.safe_load(content)

    steps = loaded["jobs"]["ai-review"]["steps"]
    for step in steps:
        assert_that(step.get("env") or {}).described_as(
            f"step {step.get('name')!r}",
        ).does_not_contain_key("ANTHROPIC_API_KEY")

    assert_that(loaded.get("env") or {}).does_not_contain_key("ANTHROPIC_API_KEY")
    assert_that(loaded["jobs"]["ai-review"].get("env") or {}).does_not_contain_key(
        "ANTHROPIC_API_KEY",
    )


def test_workflow_forbids_bare_mode_for_the_cli_transport() -> None:
    """The review step pins ``LINTRO_CLI_BARE`` to ``never``.

    ``claude --bare`` disables OAuth session login and authenticates only
    against an API key, so sending it would break the very credential this job
    runs on. AUTO would resolve the same way with no API key in scope; pinning
    it makes CI independent of that detection.
    """
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    review_steps = [
        step
        for step in loaded["jobs"]["ai-review"]["steps"]
        if str(step.get("name", "")).startswith("Run AI review")
    ]
    assert_that(review_steps).is_length(1)
    assert_that(review_steps[0]["env"]["LINTRO_CLI_BARE"]).is_equal_to("never")


def test_workflow_installs_the_cli_from_the_dockerfile_pin() -> None:
    """Agent CLI versions are resolved, not hard-coded in the workflow.

    A second pin site would drift from ``docker/ai-tools.Dockerfile``, and the
    dogfood would then review with a CLI version the contract tests never
    checked. Cursor's calendar build id is not semver, so it is resolved
    without ``--exact``.
    """
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    steps = loaded["jobs"]["ai-review"]["steps"]
    resolve_steps = [
        step for step in steps if "ai_tools_arg_pin.py" in str(step.get("run", ""))
    ]
    assert_that(resolve_steps).is_length(2)

    claude_pins = next(step for step in resolve_steps if step.get("id") == "pins")
    pin_run = claude_pins["run"]
    assert_that(pin_run).contains("NODE_VERSION")
    assert_that(pin_run).contains("CLAUDE_CODE_VERSION")
    assert_that(pin_run).contains("--exact")
    assert_that(pin_run).does_not_contain("CURSOR_AGENT_VERSION")

    cursor_pins = next(
        step for step in resolve_steps if step.get("id") == "cursor-pins"
    )
    assert_that(cursor_pins["if"]).is_equal_to(
        "${{ vars.LINTRO_AI_PROVIDER == 'cursor' }}",
    )
    assert_that(cursor_pins["run"]).contains("CURSOR_AGENT_VERSION")
    assert_that(cursor_pins["run"]).contains("CURSOR_AGENT_SHA256_X64")
    assert_that(cursor_pins["run"]).does_not_contain("--exact")

    claude_install_steps = [
        step
        for step in steps
        if str(step.get("run", "")).strip() == "scripts/ci/install-claude-cli.sh"
    ]
    assert_that(claude_install_steps).is_length(1)
    assert_that(claude_install_steps[0]["env"]["CLAUDE_CODE_VERSION"]).is_equal_to(
        "${{ steps.pins.outputs.claude-code-version }}",
    )

    cursor_install_steps = [
        step
        for step in steps
        if str(step.get("run", "")).strip() == "scripts/ci/install-cursor-agent.sh"
    ]
    assert_that(cursor_install_steps).is_length(1)
    cursor_install = cursor_install_steps[0]
    assert_that(cursor_install["if"]).is_equal_to(
        "${{ vars.LINTRO_AI_PROVIDER == 'cursor' }}",
    )
    cursor_env = cursor_install["env"]
    assert_that(cursor_env["CURSOR_AGENT_VERSION"]).is_equal_to(
        "${{ steps.cursor-pins.outputs.cursor-agent-version }}",
    )
    assert_that(cursor_env["CURSOR_AGENT_SHA256_X64"]).is_equal_to(
        "${{ steps.cursor-pins.outputs.cursor-agent-sha256-x64 }}",
    )


def test_workflow_allows_the_npm_registry_egress() -> None:
    """Harden-runner permits the endpoints the pinned CLI install needs.

    The runner blocks egress by default, so a missing endpoint turns the CLI
    install — not the review — into the failure, which reads as an unrelated
    outage.
    """
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    harden_steps = [
        step
        for step in loaded["jobs"]["ai-review"]["steps"]
        if isinstance(step.get("uses"), str)
        and step["uses"].startswith("step-security/harden-runner@")
    ]
    assert_that(harden_steps).is_length(1)

    endpoints_raw = harden_steps[0]["with"]["allowed-endpoints"]
    unconditional = endpoints_raw.replace("${{ env.AI_REVIEW_CURSOR_EGRESS }}", "")
    endpoints = unconditional.split()
    cursor_hosts = _CURSOR_EGRESS_HOSTS
    assert_that(endpoints).contains(
        "registry.npmjs.org:443",
        "nodejs.org:443",
        "release-assets.githubusercontent.com:443",
        "pipelines.actions.githubusercontent.com:443",
        "results-receiver.actions.githubusercontent.com:443",
    )
    for endpoint in endpoints:
        assert_that(endpoint).described_as(endpoint).does_not_contain("*")
        assert_that(endpoint.lower()).described_as(endpoint).does_not_contain(
            "cursor.sh",
        )
        assert_that(endpoint.lower()).described_as(endpoint).does_not_contain(
            "cursor.com",
        )

    job_env = loaded["jobs"]["ai-review"]["env"]
    cursor_egress = job_env["AI_REVIEW_CURSOR_EGRESS"]
    assert_that(cursor_egress).contains("vars.LINTRO_AI_PROVIDER == 'cursor'")
    cursor_branch = re.search(r"&&\s+'([^']+)'\s*\|\|\s*''", cursor_egress)
    if cursor_branch is None:
        pytest.fail("cursor egress expression must quote hosts and default to empty")
    parsed_hosts = set(cursor_branch.group(1).split())
    assert_that(parsed_hosts).is_equal_to(set(cursor_hosts))
    for host in parsed_hosts:
        assert_that(host).described_as(host).does_not_contain("*")
    assert_that(endpoints_raw).contains("${{ env.AI_REVIEW_CURSOR_EGRESS }}")


def test_run_ai_review_tees_under_pipefail() -> None:
    """The review pipeline must tee output and fail when the CLI exits non-zero."""
    shell_text = SHELL_SCRIPT.read_text(encoding="utf-8")
    assert_that(shell_text).contains("set -euo pipefail")
    assert_that(shell_text).contains("PYTHONUNBUFFERED=1")
    assert_that(shell_text).contains('>"$output_file" 2>&1 &')
    assert_that(shell_text).contains('kill -TERM "$lintro_pid"')
    assert_that(shell_text).contains('tail --pid="$lintro_pid"')
    assert_that(shell_text).contains("ps -o sid=")
    assert_that(shell_text).contains('"$sid" == "$child"')
    assert_that(shell_text).contains("kill -KILL")
    assert_that(shell_text).contains("trap '' TERM")
    assert_that(shell_text).contains("reaped=$?")
    assert_that(shell_text).contains("sleep 0.5")
    assert_that(shell_text).contains("[ai-review] still running")
    assert_that(shell_text).contains("persist-on-SIGTERM enabled")
    assert_that(shell_text).contains("review_state_artifacts.py")
    assert_that(shell_text).contains('upload --suffix "$1"')
    assert_that(shell_text).contains("_upload_review_state inline")
    assert_that(shell_text).contains("ckpt-${elapsed}")
    result = subprocess.run(  # nosec B603 B607 - fixed bash argv in a controlled test; binary name resolved from PATH, not attacker-controlled; shell=False
        [
            "bash",
            "-c",
            'set -euo pipefail; set +e; false | tee /dev/null; status=$?; exit "$status"',
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert_that(result.returncode).is_not_equal_to(0)


def test_term_immune_log_mirror_is_reaped_with_sigkill() -> None:
    """A ``trap '' TERM`` tail must not block wait after the review exits."""
    result = subprocess.run(  # nosec B603 B607 - fixed bash argv in a controlled test; binary name resolved from PATH, not attacker-controlled; shell=False
        [
            "bash",
            "-c",
            """
            set -euo pipefail
            tmp="$(mktemp)"
            : >"$tmp"
            ( sleep 0.2; printf '{}\\n' >"$tmp" ) &
            child=$!
            (trap '' TERM; tail --pid="$child" -n +1 -f "$tmp") &
            log_pid=$!
            wait "$child"
            status=$?
            while kill -0 "$child" 2>/dev/null; do
              wait "$child"
              status=$?
            done
            kill -KILL "$log_pid" 2>/dev/null || true
            wait "$log_pid" 2>/dev/null || true
            rm -f "$tmp"
            exit "$status"
            """,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert_that(result.returncode).is_equal_to(0)


def test_review_timeout_fits_inside_the_job_timeout() -> None:
    """The CLI transport profile timeout fires before the job ``timeout-minutes``.

    The two values are load-bearing together: when the job budget is exhausted
    first, the Actions runner kills the review mid-flight, no JSON error
    envelope is written, and ``classify_review_outcome.py`` reads a truncated
    output file (how PR #1916's review died under 600 s / 15 min). The
    invariant is ``ceil(cli_timeout / 60) + setup overhead + posting margin <=
    timeout-minutes``, with ~7 minutes observed for harden-runner + checkout +
    Node/claude install + uv sync, and a 1-minute posting margin. Bump the two
    values together — ``CLI_REVIEW_TIMEOUT_SECONDS`` in run-ai-review.sh (kept
    in sync with ``ai.transports.cli.timeout`` / DEFAULT_CLI_TIMEOUT) and
    ``timeout-minutes`` in ai-review.yml. The review invocation itself must
    not pass a hand-tuned ``--timeout`` once the profile default covers it
    (#1923).
    """
    setup_overhead_minutes = 7
    posting_margin_minutes = 1

    shell_text = SHELL_SCRIPT.read_text(encoding="utf-8")
    # Collapse backslash continuations so a flag wrapped onto its own line
    # cannot hide from the single-line assertion below.
    joined_text = shell_text.replace("\\\n", " ")
    command_lines = [
        line
        for line in joined_text.splitlines()
        if "uv run lintro review" in line and not line.lstrip().startswith("#")
    ]
    assert_that(command_lines).is_length(1)
    assert_that(command_lines[0]).does_not_contain("--timeout")

    timeout_matches = re.findall(
        r"^CLI_REVIEW_TIMEOUT_SECONDS=(\d+)\s*$",
        shell_text,
        flags=re.MULTILINE,
    )
    assert_that(timeout_matches).described_as(
        "run-ai-review.sh must declare CLI_REVIEW_TIMEOUT_SECONDS for the "
        "job-budget invariant (kept in sync with DEFAULT_CLI_TIMEOUT)",
    ).is_length(1)
    assert_that(float(timeout_matches[0])).described_as(
        "the CLI_REVIEW_TIMEOUT_SECONDS documentation variable in "
        "run-ai-review.sh has drifted from transport.DEFAULT_CLI_TIMEOUT",
    ).is_equal_to(transport.DEFAULT_CLI_TIMEOUT)
    review_timeout_minutes = math.ceil(transport.DEFAULT_CLI_TIMEOUT / 60)

    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job_timeout_minutes = loaded["jobs"]["ai-review"]["timeout-minutes"]

    budget = review_timeout_minutes + setup_overhead_minutes
    budget += posting_margin_minutes
    assert_that(job_timeout_minutes).described_as(
        f"timeout-minutes ({job_timeout_minutes}) must cover the CLI profile "
        f"timeout ({review_timeout_minutes} min) plus "
        f"{setup_overhead_minutes} min setup and "
        f"{posting_margin_minutes} min posting margin — bump it together "
        "with CLI_REVIEW_TIMEOUT_SECONDS / ai.transports.cli.timeout",
    ).is_greater_than_or_equal_to(budget)


def _ai_review_steps() -> list[Any]:
    """Return the parsed AI-review job steps.

    Returns:
        Step mappings from ``ai-review.yml``.
    """
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = loaded["jobs"]["ai-review"]["steps"]
    assert_that(steps).is_instance_of(list)
    return list(steps)


def test_workflow_locates_prior_state_via_existing_script() -> None:
    """Prior-state lookup extends ``run-ai-review.sh``, not a new script."""
    steps = _ai_review_steps()
    locate_steps = [
        step for step in steps if step.get("name") == "Locate prior review-state run"
    ]
    assert_that(locate_steps).is_length(1)
    locate = locate_steps[0]
    assert_that(locate["id"]).is_equal_to("prior-state")
    assert_that(locate.get("continue-on-error")).is_true()
    run = locate["run"]
    assert_that(run).contains("scripts/ci/review_state_artifacts.py")
    assert_that(run).contains("scripts/ci/run-ai-review.sh --locate-prior-state")
    assert_that(run).contains("GITHUB_OUTPUT")
    env = locate["env"]
    assert_that(env["PR_NUMBER"]).is_equal_to("${{ github.event.number }}")
    assert_that(env["GITHUB_REPOSITORY"]).is_equal_to("${{ github.repository }}")
    assert_that(env["GITHUB_RUN_ID"]).is_equal_to("${{ github.run_id }}")
    assert_that(env["GH_TOKEN"]).is_equal_to("${{ secrets.GITHUB_TOKEN }}")
    for credential_env in PROVIDER_CREDENTIAL_ENVS:
        assert_that(env).does_not_contain_key(credential_env)


def test_workflow_downloads_prior_state_across_runs() -> None:
    """Cross-run download uses the pinned action and skips when no run-id."""
    steps = _ai_review_steps()
    download_steps = [
        step
        for step in steps
        if isinstance(step.get("uses"), str)
        and str(step["uses"]).startswith("actions/download-artifact@")
    ]
    assert_that(download_steps).is_length(1)
    download = download_steps[0]
    assert_that(download.get("continue-on-error")).is_true()
    assert_that(download["if"]).is_equal_to("steps.prior-state.outputs.run-id != ''")
    download_with = download["with"]
    assert_that(download_with["run-id"]).is_equal_to(
        "${{ steps.prior-state.outputs.run-id }}",
    )
    assert_that(download_with["pattern"]).is_equal_to(
        "lintro-review-state-pr-${{ github.event.number }}-*",
    )
    assert_that(download_with["merge-multiple"]).is_true()
    assert_that(download_with["github-token"]).is_equal_to(
        "${{ secrets.GITHUB_TOKEN }}",
    )
    assert_that(download_with["path"]).is_equal_to("ai-review-state")


def test_workflow_uploads_state_artifacts_on_always() -> None:
    """The final upload is always() and no-ops when no state files exist."""
    steps = _ai_review_steps()
    upload_steps = [
        step
        for step in steps
        if isinstance(step.get("uses"), str)
        and str(step["uses"]).startswith("actions/upload-artifact@")
    ]
    assert_that(upload_steps).is_length(1)
    upload = upload_steps[0]
    assert_that(upload["if"]).is_equal_to("always()")
    upload_with = upload["with"]
    name = str(upload_with["name"])
    assert_that(name).contains("lintro-review-state-pr-")
    assert_that(name).contains("github.event.number")
    assert_that(name).contains("github.run_attempt")
    assert_that(name).contains("-final")
    assert_that(upload_with["path"]).is_equal_to("ai-review-state/")
    assert_that(upload_with["if-no-files-found"]).is_equal_to("ignore")
    assert_that(upload_with["retention-days"]).is_equal_to(30)


def test_workflow_keeps_mint_immediately_before_review() -> None:
    """Artifact plumbing must not sit between the App token and ``--post``."""
    steps = _ai_review_steps()
    names = [str(step.get("name", "")) for step in steps]
    mint_index = names.index("Mint lintro-review App token")
    review_index = next(
        index for index, name in enumerate(names) if name.startswith("Run AI review")
    )
    upload_index = names.index("Upload review-state artifacts")
    locate_index = names.index("Locate prior review-state run")
    download_index = names.index("Download prior review-state artifacts")
    assert_that(review_index).is_equal_to(mint_index + 1)
    assert_that(locate_index).is_less_than(mint_index)
    assert_that(download_index).is_less_than(mint_index)
    assert_that(upload_index).is_greater_than(review_index)
