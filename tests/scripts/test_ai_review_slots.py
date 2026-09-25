"""Two review slots keyed on the PR number's parity (#2796, #2627 PR 3).

The ``ai-review`` job used to hold one repo-wide concurrency slot (#2506).
It now holds one of two, ``ai-review-slot-even`` and ``ai-review-slot-odd``,
chosen by the reviewed PR number's parity (ruling 12 on #2796). Every run of
one PR, push or on-request, therefore lands in the same slot, which keeps a
push and an on-request review of that PR serialised (ruling 9 on #2795).

GitHub Actions expressions have no arithmetic operators, so parity is spelled
with ``endsWith(format('{0}', N), d)`` over the even digits. These tests pin
the exact expression and check what it selects for even and odd numbers on
both the pull-request path and the on-request path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from assertpy import assert_that

WORKFLOW = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ai-review.yml"
)

#: The reviewed PR: the event's on pull-request events, the request job's
#: validated number on an on-request review (``AI_REVIEW_PR`` reads the same).
PR_NUMBER = "github.event.pull_request.number || needs.request.outputs.pr-number"

EVEN_DIGITS = ("0", "2", "4", "6", "8")

#: The job-level group, exactly: even digits select the even slot.
SLOT_GROUP = (
    "ai-review-slot-${{ ("
    + " || ".join(
        f"endsWith(format('{{0}}', {PR_NUMBER}), '{digit}')" for digit in EVEN_DIGITS
    )
    + ") && 'even' || 'odd' }}"
)


def _workflow() -> dict[Any, Any]:
    """Return the parsed ai-review workflow.

    Returns:
        The workflow mapping.
    """
    loaded: dict[Any, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return loaded


def _flat(text: str) -> str:
    """Collapse a folded YAML scalar to single spaces.

    Args:
        text: A folded scalar as parsed.

    Returns:
        The text with every whitespace run replaced by one space.
    """
    return " ".join(text.split())


def _slot(event_pr: str, requested_pr: str) -> str:
    """Evaluate the slot the way the pinned expression does.

    ``a || b`` yields ``a`` when it is truthy, else ``b``; an absent
    ``github.event.pull_request.number`` (every ``issue_comment`` run) is the
    empty string, so the request job's number is used instead.

    Args:
        event_pr: ``github.event.pull_request.number``, or ``""``.
        requested_pr: ``needs.request.outputs.pr-number``, or ``""``.

    Returns:
        The concurrency group name.
    """
    number = event_pr or requested_pr
    parity = "even" if number.endswith(EVEN_DIGITS) else "odd"
    return f"ai-review-slot-{parity}"


def test_the_review_job_holds_one_of_two_parity_slots() -> None:
    """The job-level group is exactly the two-slot parity expression.

    ``cancel-in-progress: false`` and ``queue: max`` carry over from #2506:
    a queued review waits instead of being cancelled, and the default
    ``queue: single`` would drop the middle review of a burst.
    """
    concurrency = _workflow()["jobs"]["ai-review"]["concurrency"]

    assert_that(set(concurrency)).is_equal_to({"group", "cancel-in-progress", "queue"})
    assert_that(_flat(concurrency["group"])).is_equal_to(SLOT_GROUP)
    assert_that(concurrency["cancel-in-progress"]).is_false()
    assert_that(concurrency["queue"]).is_equal_to("max")


def test_the_repo_wide_slot_is_gone() -> None:
    """No job still names the single repo-wide group it replaced."""
    text = WORKFLOW.read_text(encoding="utf-8")

    assert_that(text).does_not_contain("group: ai-review-repo-wide")
    for job_id, job in _workflow()["jobs"].items():
        group = _flat(str((job.get("concurrency") or {}).get("group", "")))
        assert_that(group).described_as(job_id).does_not_contain("repo-wide")


def test_the_slot_reads_the_same_pr_number_as_the_review() -> None:
    """The slot and ``AI_REVIEW_PR`` key on the same number.

    If they diverged, two runs of one PR could take different slots and
    review it concurrently, racing on the sticky comment and the state.
    """
    job = _workflow()["jobs"]["ai-review"]

    assert_that(job["env"]["AI_REVIEW_PR"]).is_equal_to(f"${{{{ {PR_NUMBER} }}}}")
    assert_that(job["needs"]).is_equal_to(["request"])


@pytest.mark.parametrize(
    ("event_pr", "requested_pr", "expected"),
    [
        pytest.param("2796", "", "ai-review-slot-even", id="push-even"),
        pytest.param("2795", "", "ai-review-slot-odd", id="push-odd"),
        pytest.param("2790", "", "ai-review-slot-even", id="push-ends-in-zero"),
        pytest.param("7", "", "ai-review-slot-odd", id="push-one-digit"),
        pytest.param("", "2808", "ai-review-slot-even", id="comment-even"),
        pytest.param("", "2809", "ai-review-slot-odd", id="comment-odd"),
        # A refused request leaves no number; the expression reads "odd", but
        # the job is skipped by its `if`, so it never holds the slot.
        pytest.param("", "", "ai-review-slot-odd", id="no-pr-number"),
    ],
)
def test_the_slot_follows_the_pr_numbers_parity(
    event_pr: str,
    requested_pr: str,
    expected: str,
) -> None:
    """Even PR numbers take the even slot, odd ones the odd slot.

    Args:
        event_pr: The pull-request event's number (empty on a comment run).
        requested_pr: The request job's number (empty on a pull-request run).
        expected: The slot the run must take.
    """
    assert_that(_slot(event_pr, requested_pr)).is_equal_to(expected)


def test_a_push_and_a_request_for_one_pr_share_a_slot() -> None:
    """Both paths of one PR serialise in its slot (ruling 9 on #2795)."""
    for number in ("2796", "2809"):
        assert_that(_slot(number, "")).is_equal_to(_slot("", number))


def test_the_workflow_level_group_is_unchanged() -> None:
    """Per PR for pull-request events, per run for comments (ruling 9)."""
    concurrency = _workflow()["concurrency"]

    assert_that(_flat(concurrency["group"])).is_equal_to(
        "ai-review-${{ github.event_name == 'pull_request_target' "
        "&& github.event.pull_request.number "
        "|| format('comment-{0}', github.run_id) }}",
    )
    assert_that(concurrency["cancel-in-progress"]).is_true()
    assert_that(concurrency).does_not_contain_key("queue")


def test_the_workflow_has_no_push_or_workflow_run_trigger() -> None:
    """Only pull-request events and PR comments start a review.

    A ``push`` or ``workflow_run`` run has no PR number, so it would take
    the odd slot with an empty key and review nothing.
    """
    loaded = _workflow()
    trigger = loaded[True] if True in loaded else loaded["on"]

    assert_that(set(trigger)).is_equal_to({"pull_request_target", "issue_comment"})
