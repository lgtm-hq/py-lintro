"""The CI outcome of a round the per-PR review budget stopped (#2796).

Ruling 14 on #2796: at the budget, a round whose coverage of the head is
incomplete exits 1 with its own ``pr_budget`` outcome and headline (the next
round will not resume on its own; it stops at the same budget). A round whose
carried coverage is complete exits 0 like any complete review.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest
from assertpy import assert_that

from lintro.ai.review.pr_budget import PR_BUDGET_REASON_PREFIX

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "ci"
    / "classify_review_outcome.py"
)
_REASON = f"{PR_BUDGET_REASON_PREFIX} ($40.00) reached"


@pytest.fixture
def classifier() -> ModuleType:
    """Load the classifier script as a module.

    Returns:
        The classifier module.

    Raises:
        RuntimeError: When the module spec cannot be created.
    """
    spec = importlib.util.spec_from_file_location("classify_review_outcome", SCRIPT)
    if spec is None or spec.loader is None:
        msg = f"Unable to load module from {SCRIPT}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules["classify_review_outcome"] = module
    spec.loader.exec_module(module)
    return module


def _envelope(*, covered: int, eligible: int, stopped_reason: str) -> str:
    """Return a stopped round's JSON envelope.

    Args:
        covered: Files covered at HEAD, this round and carried.
        eligible: Files eligible for review.
        stopped_reason: Why the round stopped.

    Returns:
        The captured-output text.
    """
    complete = covered == eligible
    return json.dumps(
        {
            "readiness_verdict": "ready" if complete else "incomplete",
            "findings_coverage_complete": True,
            "coverage": {
                "reviewed": 0,
                "carried": covered,
                "awaiting": eligible - covered,
                "invalidated": 0,
                "eligible": eligible,
                "covered_at_head": covered,
                "complete": complete,
            },
            "stopped_reason": stopped_reason,
            "partial": True,
        },
    )


def test_the_prefix_matches_the_one_lintro_writes(classifier: ModuleType) -> None:
    """The script cannot import lintro, so the shared prefix is pinned here."""
    assert_that(classifier.PR_BUDGET_REASON_PREFIX).is_equal_to(
        PR_BUDGET_REASON_PREFIX,
    )


def test_a_budget_stop_with_incomplete_coverage_is_red_with_its_own_reason(
    classifier: ModuleType,
) -> None:
    """Exit 1, outcome ``pr_budget``, and a headline naming the overlay."""
    report = classifier.classify(
        status=0,
        output=_envelope(covered=3, eligible=7, stopped_reason=_REASON),
    )

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.PR_BUDGET)
    assert_that(str(report.outcome)).is_equal_to("pr_budget")
    assert_that(report.exit_code).is_equal_to(1)
    assert_that(report.headline).contains("PR review budget reached")
    assert_that(report.headline).contains("3/7 files covered at HEAD")
    assert_that(report.headline).contains("LINTRO_AI_REVIEW_PR_BUDGET_USD")
    assert_that(report.headline).does_not_contain("next round resumes")
    assert_that(report.detail).is_equal_to(_REASON)
    assert_that(report.outcome.partial_review).is_true()
    assert_that(report.outcome.produced_review).is_true()
    assert_that(report.outcome.review_unavailable).is_false()


def test_a_budget_stop_with_complete_carried_coverage_is_green(
    classifier: ModuleType,
) -> None:
    """Every file is already covered at HEAD: nothing is left unreviewed."""
    report = classifier.classify(
        status=0,
        output=_envelope(covered=7, eligible=7, stopped_reason=_REASON),
    )

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.REVIEWED)
    assert_that(report.exit_code).is_equal_to(0)


def test_a_round_cap_stop_stays_incomplete(classifier: ModuleType) -> None:
    """Only the PR-budget prefix selects the new outcome."""
    report = classifier.classify(
        status=0,
        output=_envelope(
            covered=3,
            eligible=7,
            stopped_reason="cost cap ($5.00) reached",
        ),
    )

    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.INCOMPLETE)
    assert_that(report.exit_code).is_equal_to(1)


def test_the_summary_says_what_to_do(classifier: ModuleType) -> None:
    """The step summary explains the stop and names the Actions variable."""
    report = classifier.classify(
        status=0,
        output=_envelope(covered=0, eligible=4, stopped_reason=_REASON),
    )
    summary = classifier.render_summary(report=report)

    assert_that(summary).contains("⚠️")
    assert_that(summary).contains("ai.review_pr_budget_usd")
    assert_that(summary).contains("LINTRO_AI_REVIEW_PR_BUDGET_USD")
