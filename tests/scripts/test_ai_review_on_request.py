# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Wiring tests for the on-request AI review (#2795, #2627 PR 2).

The ``issue_comment`` path adds two jobs to ``ai-review.yml``: ``request``
(the gate: no secrets, read-only token) and ``acknowledge`` (the 👀 reaction
and the constant usage text, with the App token). The review job itself is
shared with pull-request events. These tests pin the coordinator's binding
rulings: comment text never reaches a ``run:`` line, the request job holds no
secret, the acknowledge job never runs for a refused request and has no
checkout, and the review job's slot and cap are unchanged.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from assertpy import assert_that

from lintro.ai.review.commands import USAGE_TEXT

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "ai-review.yml"
_GATE_SCRIPT = "scripts/ci/resolve_review_request.py"
_PROVIDER_SECRETS = (
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CURSOR_API_KEY",
    "ZAI_AUTH_TOKEN",
    "CODEX_AUTH_JSON",
    "ANTHROPIC",
)


def _workflow() -> dict[str, Any]:
    """Return the parsed workflow.

    Returns:
        The workflow mapping.
    """
    data: dict[str, Any] = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    return data


def _job(name: str) -> dict[str, Any]:
    """Return one job.

    Args:
        name: Job id.

    Returns:
        The job mapping.
    """
    job: dict[str, Any] = _workflow()["jobs"][name]
    return job


def _flat(expression: str) -> str:
    """Collapse an expression's whitespace for exact comparison.

    Args:
        expression: A (possibly folded) workflow expression.

    Returns:
        The expression on one line with single spaces.
    """
    return " ".join(expression.split())


def _checkouts(job: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a job's checkout steps.

    Args:
        job: The job mapping.

    Returns:
        The steps using ``actions/checkout``.
    """
    return [
        step
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout@")
    ]


def test_the_comment_trigger_is_created_comments_only() -> None:
    """Edited or deleted comments never start a review."""
    triggers = _workflow()["on"]

    assert_that(triggers["issue_comment"]).is_equal_to({"types": ["created"]})
    assert_that(triggers["pull_request_target"]["types"]).is_equal_to(
        ["opened", "synchronize", "reopened", "ready_for_review"],
    )


def test_a_comment_can_never_cancel_a_review() -> None:
    """Comment runs get their own group; nothing a comment says selects it.

    Ruling 9 (#2795, from a CodeRabbit finding): workflow-level concurrency is
    evaluated before the permission check, so the group must not depend on the
    comment's author association or text, and every issue_comment run must
    land in a group keyed on its own run id.
    """
    group = _flat(_workflow()["concurrency"]["group"])

    assert_that(group).does_not_contain("author_association")
    assert_that(group).does_not_contain("comment.body")
    assert_that(group).does_not_contain("github.event.issue")
    assert_that(group).contains("github.event_name == 'pull_request_target'")
    assert_that(group).contains("format('comment-{0}', github.run_id)")


def test_no_run_line_or_input_ever_reads_the_comment_or_the_issue() -> None:
    """Comment text reaches steps only through ``env:`` (ruling 4).

    A ``${{ github.event.comment.* }}`` or ``${{ github.event.issue.* }}``
    template is expanded before the step runs: inside ``run:`` into the shell,
    and inside ``with:`` into an action's inputs (a github-script body is
    code). Both are scanned for every step of every job.
    """
    for job_id, job in _workflow()["jobs"].items():
        for step in job.get("steps", []):
            where = f"{job_id}: {step.get('name')}"
            expanded = str(step.get("run", "")) + yaml.safe_dump(step.get("with") or {})
            assert_that(expanded).described_as(where).does_not_contain(
                "github.event.comment",
            )
            assert_that(expanded).described_as(where).does_not_contain(
                "github.event.issue",
            )


def test_the_request_job_is_the_secret_free_gate() -> None:
    """Read-only token, no secret, a trusted checkout of the gate (ruling 7)."""
    job = _job("request")

    assert_that(_flat(job["if"])).is_equal_to(
        "github.event_name == 'issue_comment' && github.event.issue.pull_request "
        "&& startsWith(github.event.comment.body, '@lintro review')",
    )
    assert_that(job["permissions"]).is_equal_to(
        {"contents": "read", "pull-requests": "read"},
    )
    assert_that(yaml.safe_dump(job)).does_not_contain("secrets.")
    (checkout,) = _checkouts(job)
    options = checkout["with"]
    assert_that(options["ref"]).is_equal_to("${{ github.workflow_sha }}")
    assert_that(options["fetch-depth"]).is_equal_to(1)
    assert_that(options["persist-credentials"]).is_false()
    # Non-cone: the entries are file paths, not directory prefixes.
    assert_that(options["sparse-checkout-cone-mode"]).is_false()
    assert_that(options["sparse-checkout"].split()).is_equal_to(
        ["lintro/ai/review/commands.py", _GATE_SCRIPT],
    )
    (resolve,) = [step for step in job["steps"] if step.get("id") == "resolve"]
    assert_that(resolve["run"]).is_equal_to(f"python3 {_GATE_SCRIPT}")
    assert_that(resolve["env"]["COMMENT_BODY"]).is_equal_to(
        "${{ github.event.comment.body }}",
    )
    assert_that(resolve["env"]["GH_TOKEN"]).is_equal_to("${{ github.token }}")


#: The request job's outputs, exactly the names the gate script writes
#: (the gate's own tests pin the written names in the same order).
_REQUEST_OUTPUTS = (
    "run",
    "mode",
    "pr-number",
    "paths",
    "comment-id",
    "requester",
)


def test_the_request_outputs_are_routed_exactly() -> None:
    """Every consumer reads a declared output; each output is the gate's.

    ``acknowledge`` and ``ai-review`` read ``needs.request.outputs.<name>``
    for pr-number, comment-id, mode, paths and requester; a typo in any of
    them would silently read an empty string.
    """
    outputs = _job("request")["outputs"]
    assert_that(tuple(outputs)).is_equal_to(_REQUEST_OUTPUTS)
    for name, value in outputs.items():
        assert_that(value).is_equal_to(f"${{{{ steps.resolve.outputs.{name} }}}}")

    read = _outputs_read(_WORKFLOW.read_text(encoding="utf-8"))
    # Every declared output is read somewhere: an unread output (head-sha
    # until #2796) is dead routing that a later reader would trust.
    assert_that(read).is_equal_to(set(_REQUEST_OUTPUTS))


#: ``needs.request.outputs.<name>`` in dot form, or ``['<name>']`` /
#: ``["<name>"]`` in bracket form; both are valid expression syntax.
_OUTPUT_READ = re.compile(
    r"""needs\.request\.outputs(?:\.([a-z-]+)|\[\s*['"]([a-z-]+)['"]\s*\])""",
)


def _outputs_read(text: str) -> set[str]:
    """Return the request-job output names that ``text`` reads.

    Args:
        text: Workflow source.

    Returns:
        The output names read, in dot or bracket form.
    """
    return {dot or bracket for dot, bracket in _OUTPUT_READ.findall(text)}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("${{ needs.request.outputs.mode }}", {"mode"}, id="dot"),
        pytest.param(
            "${{ needs.request.outputs['pr-number'] }}",
            {"pr-number"},
            id="bracket-single-quote",
        ),
        pytest.param(
            '${{ needs.request.outputs[ "paths" ] }}',
            {"paths"},
            id="bracket-double-quote",
        ),
        pytest.param("${{ needs.other.outputs.mode }}", set(), id="other-job"),
    ],
)
def test_output_reads_are_found_in_dot_and_bracket_form(
    text: str,
    expected: set[str],
) -> None:
    """A bracket-form read cannot slip past the routing check.

    Args:
        text: A workflow fragment.
        expected: The output names it reads.
    """
    assert_that(_outputs_read(text)).is_equal_to(expected)


def test_the_gate_script_writes_exactly_the_declared_outputs() -> None:
    """The names the gate writes on an accepted request are the job's outputs."""
    tree = ast.parse((_REPO_ROOT / _GATE_SCRIPT).read_text(encoding="utf-8"))
    accepted = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
        and any(
            isinstance(key, ast.Constant)
            and key.value == "run"
            and isinstance(value, ast.Constant)
            and value.value == "true"
            for key, value in zip(node.keys, node.values, strict=True)
        )
    ]

    assert_that(accepted).described_as("accepted-request output dict").is_length(1)
    written = [
        str(key.value) for key in accepted[0].keys if isinstance(key, ast.Constant)
    ]
    assert_that(sorted(written)).is_equal_to(sorted(_REQUEST_OUTPUTS))


def test_the_gate_script_is_invoked_only_by_the_request_job() -> None:
    """The review job checks out an open PR's base.sha, which may predate it."""
    for job_id, job in _workflow()["jobs"].items():
        mentions = _GATE_SCRIPT in yaml.safe_dump(job.get("steps", []))
        assert_that(mentions).described_as(job_id).is_equal_to(job_id == "request")


def test_acknowledge_never_runs_for_a_refused_request() -> None:
    """Refusals are log-only; only accepted and usage modes are acknowledged."""
    job = _job("acknowledge")

    assert_that(job["needs"]).is_equal_to(["request"])
    assert_that(_flat(job["if"])).is_equal_to(
        'contains(fromJSON(\'["full","delta","paths","usage"]\'), '
        "needs.request.outputs.mode)",
    )
    assert_that(job["if"]).does_not_contain("refused")


def test_acknowledge_has_no_checkout_no_provider_secret_and_no_comment_text() -> None:
    """Only integers and the validated mode reach the acknowledge step."""
    job = _job("acknowledge")
    dumped = yaml.safe_dump(job)

    assert_that(_checkouts(job)).is_empty()
    assert_that(job["permissions"]).is_equal_to({})
    for secret in _PROVIDER_SECRETS:
        assert_that(dumped).does_not_contain(secret)
    assert_that(dumped).does_not_contain("github.event.comment")
    (script_step,) = [
        step
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("actions/github-script@")
    ]
    assert_that(script_step["env"]).is_equal_to(
        {
            "COMMENT_ID": "${{ needs.request.outputs.comment-id }}",
            "PR_NUMBER": "${{ needs.request.outputs.pr-number }}",
            "REQUEST_MODE": "${{ needs.request.outputs.mode }}",
        },
    )


def test_the_usage_reply_is_the_parsers_usage_text() -> None:
    """The workflow's constant reply and the parser's text cannot drift.

    Compared in both directions: the reply's lines, in order, are exactly
    ``USAGE_TEXT``'s lines, so neither side can gain or lose a line alone.
    """
    script = _job("acknowledge")["steps"][-1]["with"]["script"]
    array = script.split("body: [", 1)[1].split("].join(", 1)[0]
    reply_lines = [
        line.strip().removesuffix(",").removeprefix("'").removesuffix("'")
        for line in array.strip().splitlines()
    ]

    assert_that(reply_lines).is_equal_to(USAGE_TEXT.split("\n"))


def test_the_review_job_runs_on_both_paths_with_validated_inputs() -> None:
    """The shared review job: request outputs only, a review slot, the 53 min cap."""
    job = _job("ai-review")

    assert_that(job["needs"]).is_equal_to(["request"])
    assert_that(_flat(job["if"])).is_equal_to(
        "!cancelled() && ((github.event_name == 'pull_request_target' "
        "&& github.event.pull_request.draft == false "
        "&& github.event.pull_request.head.repo.full_name == github.repository) "
        "|| (github.event_name == 'issue_comment' "
        "&& needs.request.outputs.run == 'true'))",
    )
    (review,) = [
        step
        for step in job["steps"]
        if step.get("run") == "scripts/ci/run-ai-review.sh"
    ]
    assert_that(review["env"]["REVIEW_REQUEST_MODE"]).is_equal_to(
        "${{ needs.request.outputs.mode }}",
    )
    assert_that(review["env"]["REVIEW_REQUEST_PATHS"]).is_equal_to(
        "${{ needs.request.outputs.paths }}",
    )
    assert_that(review["env"]["REVIEW_REQUESTER"]).is_equal_to(
        "${{ needs.request.outputs.requester }}",
    )
    assert_that(job["timeout-minutes"]).is_equal_to(53)
    # The slot expression itself is pinned in test_ai_review_slots.py.
    assert_that(job["concurrency"]["group"]).starts_with("ai-review-slot-")
    assert_that(job["concurrency"]["cancel-in-progress"]).is_false()
    assert_that(job["concurrency"]["queue"]).is_equal_to("max")
