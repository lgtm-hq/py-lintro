"""Checklist confirmations never become findings (issue #2430).

The prompt used to require a finding for every checklist "yes", so a reviewer
that verified a change as correct emitted a finding whose body said it was
not a defect. The rule is gone from the prompts, and the parser drops any such
finding that a model still produces.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.ai.providers.response import AIResponse
from lintro.ai.review.cli_limits import findings_cap_was_hit
from lintro.ai.review.confirmation_filter import (
    drop_confirmation_findings,
    is_confirmation_finding,
)
from lintro.ai.review.merge import parse_review_response
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.response_pipeline import payload_to_partial

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
HOMEBREW_TAP_411_FIXTURE = FIXTURE_DIR / "homebrew_tap_411_confirmation.json"

_DEFECT_DESCRIPTION = (
    "P2 because the documented contract is false: the flag is parsed but never "
    "read, so the documented default is not the effective one."
)


def _response(*, content: str = "{}") -> AIResponse:
    """Build a provider response stub for payload parsing.

    Args:
        content: Raw response text the payload was parsed from.

    Returns:
        A response carrying the usage fields the parser reads.
    """
    return AIResponse(
        content=content,
        model="test-model",
        input_tokens=10,
        output_tokens=20,
        cost_estimate=0.01,
    )


def _finding(
    *,
    title: str = "Flag parsed but never read",
    description: str = _DEFECT_DESCRIPTION,
    fix: str = "Read the flag in resolve_config before applying defaults.",
) -> ReviewFinding:
    """Build a finding with defect-shaped text unless overridden.

    Args:
        title: Finding title.
        description: Finding description.
        fix: Finding fix text.

    Returns:
        A P2 finding on a fixed file and line.
    """
    return ReviewFinding(
        severity=Severity.P2,
        category="logic-bug",
        file="src/config.py",
        line=12,
        title=title,
        description=description,
        cause="The parser stores the value on an unused attribute.",
        fix=fix,
        confidence="high",
        checklist_ids=(3,),
    )


def test_homebrew_tap_411_fixture_yields_no_findings_and_one_checklist_yes() -> None:
    """The recorded homebrew-tap#411 response parses to zero findings.

    The finding in the fixture is the one lintro posted as an inline thread on
    lgtm-hq/homebrew-tap#411: its body says "positive verification, not a
    defect" and its fix is "No code change". The checklist entry that
    produced it stays, so the answer is still visible as a cleared item.
    """
    raw = HOMEBREW_TAP_411_FIXTURE.read_text(encoding="utf-8")

    payload = parse_review_response(content=raw)
    partial = payload_to_partial(response=_response(content=raw), payload=payload)

    assert_that(partial.findings).is_empty()
    yes_answers = [item for item in partial.checklist if item.answer == "yes"]
    assert_that(yes_answers).is_length(1)
    assert_that(yes_answers[0].id).is_equal_to(8)
    assert_that(partial.checklist).is_length(3)


def test_homebrew_tap_411_fixture_carries_the_posted_finding_verbatim() -> None:
    """The fixture holds the exact finding text quoted in the issue.

    Guards the regression test against a later edit that quietly makes the
    fixture easier to drop than the real response was.
    """
    payload = json.loads(HOMEBREW_TAP_411_FIXTURE.read_text(encoding="utf-8"))
    finding = payload["findings"][0]

    assert_that(finding["title"]).is_equal_to("Bump matches the claimed 0.23.0 update")
    assert_that(finding["description"]).starts_with(
        "Checklist item 8 is a positive verification, not a defect",
    )
    assert_that(finding["fix"]).starts_with("No code change")
    assert_that(finding["category"]).is_equal_to("logic-bug")


def test_dropped_confirmation_does_not_count_toward_findings_cap() -> None:
    """A dropped confirmation is gone before the cap is checked.

    ``chunk_pass`` counts ``len(partial.findings)`` after
    ``payload_to_partial``, so a cap of one is not hit by a chunk whose only
    finding was a confirmation.
    """
    raw = HOMEBREW_TAP_411_FIXTURE.read_text(encoding="utf-8")
    payload = parse_review_response(content=raw)
    assert_that(payload["findings"]).is_length(1)

    partial = payload_to_partial(response=_response(content=raw), payload=payload)

    assert_that(
        findings_cap_was_hit(findings_count=len(partial.findings), findings_cap=1),
    ).is_false()


@pytest.mark.parametrize(
    "description",
    [
        "This is a positive verification, not a defect.",
        "NOT A DEFECT: the diff does what the PR claims.",
        "P3 because the code path is correct; no code change is needed.",
        "Checklist item 4 is a Positive Verification of the retry path.",
        "This is a confirmation that the sha256 matches.",
        "Recorded as confirmation only.",
    ],
)
def test_confirmation_phrase_in_description_is_dropped(description: str) -> None:
    """Any listed phrase in the description marks the finding a confirmation.

    Args:
        description: Description text carrying one of the phrases.
    """
    finding = _finding(description=description)

    assert_that(is_confirmation_finding(finding=finding)).is_true()


@pytest.mark.parametrize(
    "fix",
    [
        "No code change",
        "no code change",
        "  No Code Change  ",
        "No code change; treat as confirmation that url/sha256 match.",
        "None needed — this is a confirmation of existing behavior.",
    ],
)
def test_confirmation_phrase_in_fix_is_dropped(fix: str) -> None:
    """A confirmation-shaped fix drops the finding even with a defect description.

    Args:
        fix: Fix text that is exactly, or contains, a listed phrase.
    """
    finding = _finding(fix=fix)

    assert_that(is_confirmation_finding(finding=finding)).is_true()


@pytest.mark.parametrize(
    ("description", "fix"),
    [
        (_DEFECT_DESCRIPTION, "Read the flag before applying defaults."),
        (
            "P2 because the handler returns success after skipping the work.",
            "Return the error status when the upload is skipped.",
        ),
        (
            "The unconfirmed write is retried without a backoff.",
            "Add exponential backoff to the retry loop.",
        ),
    ],
)
def test_defect_findings_are_kept(description: str, fix: str) -> None:
    """Findings that describe a defect pass through untouched.

    ``unconfirmed`` does not match ``confirmation``: the phrases match on word
    boundaries, so a defect about an unconfirmed write is not a confirmation.

    Args:
        description: Defect description.
        fix: Actionable fix text.
    """
    finding = _finding(description=description, fix=fix)

    assert_that(is_confirmation_finding(finding=finding)).is_false()
    assert_that(drop_confirmation_findings(findings=(finding,))).is_equal_to(
        (finding,),
    )


def test_drop_keeps_order_and_only_removes_confirmations() -> None:
    """Dropping is order-preserving and leaves defects alone."""
    first = _finding(title="first")
    confirmation = _finding(title="verified", fix="No code change")
    last = _finding(title="last")

    kept = drop_confirmation_findings(findings=(first, confirmation, last))

    assert_that(kept).is_equal_to((first, last))


def test_drop_logs_each_confirmation_title_at_debug() -> None:
    """Every drop is recorded at debug level with the finding title."""
    messages: list[str] = []
    handler_id = logger.add(
        lambda message: messages.append(str(message)),
        level="DEBUG",
    )
    try:
        drop_confirmation_findings(
            findings=(
                _finding(
                    title="Bump matches the claimed 0.23.0 update",
                    fix="No code change",
                ),
                _finding(title="kept defect"),
            ),
        )
    finally:
        logger.remove(handler_id)

    joined = "".join(messages)
    assert_that(joined).contains("Bump matches the claimed 0.23.0 update")
    assert_that(joined).contains("DEBUG")
    assert_that(joined).does_not_contain("kept defect")


def test_severity_is_untouched_for_kept_findings() -> None:
    """The filter drops or keeps; it never remaps severity."""
    raw_findings = [
        {
            "severity": "P1",
            "category": "logic-bug",
            "file": "src/config.py",
            "line": 3,
            "title": "Fail-open on missing token",
            "description": "P1 because the auth check is skipped when the token is empty.",
            "failure_scenario": "Empty token header, request served with 200.",
            "fix": "Reject an empty token.",
            "confidence": "high",
        },
    ]

    partial = payload_to_partial(
        response=_response(),
        payload={"summary": "s", "checklist": [], "findings": raw_findings},
    )

    assert_that(partial.findings).is_length(1)
    assert_that(partial.findings[0].severity).is_equal_to(Severity.P1)
