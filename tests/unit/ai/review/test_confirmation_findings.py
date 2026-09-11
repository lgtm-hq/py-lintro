"""Checklist confirmations never become findings (issue #2430).

The prompt used to require a finding for every checklist "yes", so a reviewer
that verified a change as correct emitted a finding whose body said it was
not a defect. The rule is gone from the prompts, and the parser drops any such
finding that a model still produces.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.providers.response import AIResponse
from lintro.ai.review.cli_limits import findings_cap_was_hit
from lintro.ai.review.confirmation_filter import (
    drop_confirmation_findings,
    is_confirmation_finding,
)
from lintro.ai.review.enums.coverage_degradation_reason import (
    CoverageDegradationReason,
)
from lintro.ai.review.enums.finding_kind import FindingKind
from lintro.ai.review.merge import parse_review_response
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.orchestrator import run_review_async
from lintro.ai.review.response_pipeline import payload_to_partial
from lintro.ai.review.response_recovery import (
    UNSTRUCTURED_CATEGORY,
    unstructured_review_payload,
)
from lintro.ai.review.session import ReviewSessionOptions

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
    kind: FindingKind = FindingKind.FINDING,
) -> ReviewFinding:
    """Build a finding with defect-shaped text unless overridden.

    Args:
        title: Finding title.
        description: Finding description.
        fix: Finding fix text.
        kind: Whether the entry is a defect claim or an open question.

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
        kind=kind,
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
        "Not a defect.",
        "Checklist item 4 is a Positive Verification of the retry path.",
        "This is a confirmation that the sha256 matches.",
    ],
)
def test_self_classifying_description_sentence_is_dropped(description: str) -> None:
    """A description sentence that opens as a classification drops the finding.

    Args:
        description: Description whose first or a later sentence opens with
            the classification.
    """
    finding = _finding(description=description)

    assert_that(is_confirmation_finding(finding=finding)).is_true()


@pytest.mark.parametrize(
    "fix",
    [
        "No code change",
        "no code change",
        "  No Code Change  ",
        "No code change.",
        "No code change needed.",
        "No code change required",
        "None.",
        "none",
    ],
)
def test_whole_field_no_code_change_fix_is_dropped(fix: str) -> None:
    """A fix that is nothing but "no code change" or "none" drops the finding.

    Args:
        fix: Fix text whose whole field is the non-actionable marker.
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
        (
            "P2 because the confirmation dialog never opens when the token has "
            "expired, so the delete runs without confirmation.",
            "Open the confirmation dialog before checking token expiry.",
        ),
        (
            "P2 because the deploy script treats HTTP 200 as a confirmation of "
            "rollback success even when the body reports a failed rollback.",
            "Parse the response body and fail the deploy on a failed rollback.",
        ),
        (
            "P3 because the README still documents the removed --strict flag; "
            "this is wording.",
            "Update the README; no code change needed.",
        ),
        (
            "P3 because the retry comment is stale; this is wording.",
            "No code change is needed in the loop, but reword the comment.",
        ),
        (
            "P2 because the migration is not a defect-free path: it drops the "
            "index before the backfill finishes.",
            "Create the new index before dropping the old one.",
        ),
        (
            "Confirmation emails are never sent when the queue is paused.",
            "Flush the queue before returning from the pause handler.",
        ),
        (
            "Confirmation prompts are skipped when --yes is unset.",
            "Prompt unless --yes was passed.",
        ),
        (
            "This is a confirmation dialog that never opens on expired tokens.",
            "Open the dialog before checking token expiry.",
        ),
        (
            "Positive verification of the checksum is skipped for cached wheels.",
            "Verify the checksum on cache hits too.",
        ),
        (
            "Positive verification: the workflow input reaches the script.",
            "Assert the input in the wiring test.",
        ),
        (
            "The bump is correct. Confirmation of the claimed 0.23.0 update.",
            "Pin the version in the formula test.",
        ),
    ],
)
def test_defect_findings_are_kept(description: str, fix: str) -> None:
    """Findings that describe a defect pass through untouched.

    Phrases inside defect prose are not classifications: a broken
    confirmation dialog, a deploy that mis-treats a response "as a
    confirmation of" success, a docs-only fix that mentions "no code change"
    mid-instruction, and "not a defect-free path" mid-sentence all keep their
    findings.

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


def test_drop_logs_each_confirmation_title_at_info() -> None:
    """Every drop is recorded at info level with the finding title."""
    messages: list[str] = []
    handler_id = logger.add(
        lambda message: messages.append(str(message)),
        level="INFO",
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
    assert_that(joined).contains("INFO")
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


async def test_dropped_confirmation_records_no_cap_degradation_through_chunk_pass(
    tmp_path: Path,
) -> None:
    """A one-chunk CLI run whose only finding is the fixture confirmation is uncapped.

    Runs the real orchestrator with a per-call cap of 1 and the recorded
    homebrew-tap#411 answer replayed as the provider response. The answer
    carries exactly one finding, which would hit the cap if it were counted
    before the drop; the run must record no ``FINDINGS_CAP_APPLIED``
    degradation and post no findings.

    Args:
        tmp_path: Pytest temporary directory fixture, used as the repo root.
    """
    raw = HOMEBREW_TAP_411_FIXTURE.read_text(encoding="utf-8")
    context = ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(
                path="Formula/winnow.rb",
                status="modified",
                additions=2,
                deletions=2,
            ),
        ],
        unified_diff='-  url "v0.22.0.tar.gz"\n+  url "v0.23.0.tar.gz"\n',
        pr_metadata=None,
        repo_root=str(tmp_path),
    )
    provider = MagicMock()
    provider.aclose = AsyncMock()
    provider.model_name = "claude-sonnet-4-6"
    provider.name = "anthropic"
    provider.capabilities.supports_sessions = False

    with patch(
        "lintro.ai.review.provider_call.call_ai",
        new=AsyncMock(return_value=_response(content=raw)),
    ):
        result = await run_review_async(
            context=context,
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(
                    enabled=True,
                    review=True,
                    transport=AITransport.CLI,
                    cli_max_findings_per_call=1,
                ),
                depth=1,
                checklist_items=[],
                checklist_text="",
                classifications=[],
            ),
        )

    assert_that(result.findings).is_empty()
    reasons = [item.reason for item in result.metadata.coverage_degradations]
    assert_that(reasons).does_not_contain(
        CoverageDegradationReason.FINDINGS_CAP_APPLIED,
    )
    assert_that(result.metadata.findings_cap_applied).is_none()
    assert_that(result.metadata.findings_coverage_complete).is_true()


def test_prose_recovery_finding_is_never_dropped() -> None:
    """The unstructured-output finding survives even when its prose says "not a defect".

    Its description is the model's whole answer, so a phrase inside it says
    nothing about the finding as a whole; dropping it would discard every
    real finding the prose carried.
    """
    payload = unstructured_review_payload(
        content=(
            "Item 3 is not a defect. However, the handler returns 200 after "
            "skipping the upload, which is a P2."
        ),
        files=("src/upload.py",),
    )

    partial = payload_to_partial(response=_response(), payload=payload)

    assert_that(partial.findings).is_length(1)
    assert_that(partial.findings[0].category).is_equal_to(UNSTRUCTURED_CATEGORY)


@pytest.mark.parametrize(
    ("description", "fix"),
    [
        ("Not a defect, but should the retry budget be configurable?", "None"),
        ("This is a confirmation that the flag is read; is the default right?", "None"),
    ],
)
def test_questions_are_never_dropped(description: str, fix: str) -> None:
    """A question keeps its place even when its prose looks like a confirmation."""
    finding = _finding(description=description, fix=fix, kind=FindingKind.QUESTION)

    assert_that(is_confirmation_finding(finding=finding)).is_false()
    assert_that(drop_confirmation_findings(findings=(finding,))).is_equal_to((finding,))
