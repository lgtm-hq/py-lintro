"""Tests for the per-round verification pass (issue #2728, step 0.11).

One provider call per round, after synthesis and before the severity gates,
asks the model to *refute* the findings that decide the verdict: every P1
and, by default, every low-confidence finding. Refuted findings leave the
round with their evidence recorded; a P1 whose failure scenario did not hold
becomes P2 with its own downgrade reason; everything else is kept and marked
verified. The pass is fail-soft and never touches custom-agent findings.

The conftest stubs the pass for every other module; the ``verification``
marker here lets the real one run.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import ExitStack
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.budget import CostBudget
from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import AIError, AITurnLimitError
from lintro.ai.prompts.review import REVIEW_VERIFICATION_SYSTEM_PROMPT
from lintro.ai.providers.capabilities import ProviderCapabilities
from lintro.ai.providers.response import AIResponse
from lintro.ai.review.enums.coverage_degradation_reason import (
    CoverageDegradationReason,
)
from lintro.ai.review.enums.evidence_style import EvidenceStyle
from lintro.ai.review.enums.finding_kind import FindingKind
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.enums.severity_downgrade_reason import SeverityDowngradeReason
from lintro.ai.review.enums.verification_outcome import VerificationOutcome
from lintro.ai.review.github_notes import format_verification_note_line
from lintro.ai.review.group_labels import REL_SINGLE_FILE
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.merged_duplicate import MergedDuplicate
from lintro.ai.review.models.review_chunk import ReviewChunk
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.models.verification_outcome import (
    RefutedFinding,
    VerificationSummary,
)
from lintro.ai.review.orchestrator import run_review
from lintro.ai.review.output import review_result_to_dict
from lintro.ai.review.repo_context import RepoContextSource
from lintro.ai.review.result_assembly import ReviewRunOutcome
from lintro.ai.review.run_finalize import _verify_and_gate, gate_built_in_findings
from lintro.ai.review.run_record_factory import RoundTotals, run_record_from_result
from lintro.ai.review.session import ReviewSessionOptions
from lintro.ai.review.timings import ReviewPhase
from lintro.ai.review.verification import (
    MAX_VERIFICATION_FINDINGS,
    VerificationPassRequest,
    run_verification_pass,
    select_for_verification,
    verification_degradations,
)
from lintro.ai.review.verification_note import format_verification_note
from lintro.ai.review.verification_prompt import render_verification_findings
from lintro.ai.review.verification_response import (
    cites_finding,
    parse_verification_answer,
)
from lintro.config.review_config import ReviewConfig, ReviewVerifyMode

pytestmark = pytest.mark.verification

_LEAKED_KEY = "sk-abcdefghijklmnopqrstuvwxyz0123456789"  # nosec B105 — fixture
_DIFF = (
    "diff --git a/pkg/api.py b/pkg/api.py\n"
    "--- a/pkg/api.py\n+++ b/pkg/api.py\n"
    "@@ -1,2 +1,2 @@\n-def send(payload):\n+def send(payload, *, retries):\n"
)


def _finding(**overrides: Any) -> ReviewFinding:
    """Build a high-confidence P1 with a failure scenario.

    Args:
        **overrides: Fields to override on the base finding.

    Returns:
        The constructed finding.
    """
    fields: dict[str, Any] = {
        "severity": Severity.P1,
        "category": "logic-bug",
        "file": "pkg/api.py",
        "line": 2,
        "title": "Retries never applied",
        "description": "The new keyword is ignored.",
        "cause": "It is not read.",
        "fix": "Read it.",
        "confidence": "high",
        "failure_scenario": "Every call runs once.",
        "evidence_style": EvidenceStyle.DIFF_LOCAL,
        "evidence_claimed": True,
    }
    fields.update(overrides)
    return ReviewFinding(**fields)


def _answer(*items: tuple[int, str, str]) -> str:
    """Build a verifier answer.

    Args:
        *items: ``(index, outcome, evidence)`` triples.

    Returns:
        JSON text in the schema the pass asks for.
    """
    return json.dumps(
        {
            "verifications": [
                {"index": index, "outcome": outcome, "evidence": evidence}
                for index, outcome, evidence in items
            ],
        },
    )


def _response(*, content: str) -> AIResponse:
    """Wrap raw text as a provider response.

    Args:
        content: Response body.

    Returns:
        An ``AIResponse`` with fixed usage counters.
    """
    return AIResponse(
        content=content,
        model="m",
        input_tokens=100,
        output_tokens=50,
        cost_estimate=0.01,
        provider="anthropic",
    )


def _context() -> ReviewContext:
    """Build a one-file context.

    Returns:
        A review context over ``pkg/api.py``.
    """
    return ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(path="pkg/api.py", status="modified", additions=1, deletions=1),
        ],
        unified_diff=_DIFF,
        pr_metadata=None,
    )


async def _pass(
    *,
    findings: list[ReviewFinding],
    content: str | None = None,
    error: Exception | None = None,
    mode: ReviewVerifyMode = ReviewVerifyMode.P1_AND_LOW_CONFIDENCE,
    stop: asyncio.Event | None = None,
    prompts: list[str] | None = None,
) -> tuple[Any, AsyncMock]:
    """Run the pass with the provider call mocked.

    Args:
        findings: The round's findings.
        content: Answer text the call returns.
        error: Exception the call raises instead.
        mode: Verify mode.
        stop: Optional interrupt event.
        prompts: Optional sink for the user prompt.

    Returns:
        The pass result and the call mock.
    """

    async def _call(*, user_prompt: str, **_kwargs: Any) -> AIResponse:
        if prompts is not None:
            prompts.append(user_prompt)
        if stop is not None and stop.is_set():
            await asyncio.sleep(60)
        if error is not None:
            raise error
        return _response(content=content or _answer())

    call = AsyncMock(side_effect=_call)
    with patch("lintro.ai.review.provider_call.call_ai", call):
        result = await run_verification_pass(
            request=VerificationPassRequest(
                context=_context(),
                findings=findings,
                mode=mode,
                provider=MagicMock(),
                ai_config=AIConfig(enabled=True, transport=AITransport.API),
                budget=CostBudget(max_cost_usd=None),
                stop=stop,
            ),
        )
    return result, call


# --- selection ----------------------------------------------------------------


def test_selection_takes_p1s_first_then_low_confidence_by_band() -> None:
    """P1s lead regardless of confidence; low-confidence P2s precede P3s."""
    findings = [
        _finding(severity=Severity.P3, confidence="low"),
        _finding(severity=Severity.P2, confidence="low"),
        _finding(severity=Severity.P2, confidence="high"),
        _finding(severity=Severity.P1, confidence="high"),
        _finding(severity=Severity.P1, confidence="low"),
    ]

    indices = select_for_verification(
        findings=findings,
        mode=ReviewVerifyMode.P1_AND_LOW_CONFIDENCE,
    )

    assert_that(indices).is_equal_to((3, 4, 1, 0))


def test_p1_mode_selects_only_p1s_and_off_selects_nothing() -> None:
    """The narrower mode ignores confidence; ``off`` ignores everything."""
    findings = [
        _finding(severity=Severity.P2, confidence="low"),
        _finding(severity=Severity.P1),
    ]

    assert_that(
        select_for_verification(findings=findings, mode=ReviewVerifyMode.P1),
    ).is_equal_to((1,))
    assert_that(
        select_for_verification(findings=findings, mode=ReviewVerifyMode.OFF),
    ).is_empty()


def test_questions_are_never_selected() -> None:
    """A question carries no severity and never moves the verdict."""
    findings = [_finding(kind=FindingKind.QUESTION, confidence="low")]

    assert_that(
        select_for_verification(
            findings=findings,
            mode=ReviewVerifyMode.P1_AND_LOW_CONFIDENCE,
        ),
    ).is_empty()


def test_selection_is_capped_with_the_least_consequential_cut_first() -> None:
    """Past the cap, low-confidence P3s are the ones left unverified."""
    findings = [_finding(severity=Severity.P3, confidence="low")] * 5 + [
        _finding(severity=Severity.P1),
    ] * MAX_VERIFICATION_FINDINGS

    indices = select_for_verification(
        findings=findings,
        mode=ReviewVerifyMode.P1_AND_LOW_CONFIDENCE,
    )

    assert_that(indices).is_length(MAX_VERIFICATION_FINDINGS)
    assert_that(min(indices)).is_equal_to(5)


# --- prompt -------------------------------------------------------------------


def test_rendered_findings_are_fenced_and_redacted() -> None:
    """Every untrusted byte sits inside the boundary fence, secrets removed."""
    source = RepoContextSource(
        reader=lambda _path: f"def send(payload, *, retries):\n    key = '{_LEAKED_KEY}'\n",
    )
    findings = [_finding(description=f"leaks {_LEAKED_KEY}")]

    rendered = render_verification_findings(
        findings=findings,
        indices=(0,),
        source=source,
        boundary="FENCE_1",
        allowed_paths=frozenset({"pkg/api.py"}),
    )

    assert_that(rendered).starts_with("Finding 1:\n<FENCE_1>\n")
    assert_that(rendered).ends_with("</FENCE_1>")
    assert_that(rendered).contains("pkg/api.py (post-change, around line 2)")
    assert_that(rendered).does_not_contain(_LEAKED_KEY)
    assert_that(rendered).contains("[REDACTED]")


def test_rendered_findings_omit_cited_code_without_a_source() -> None:
    """No reader means the findings go alone, not with an empty code block."""
    rendered = render_verification_findings(
        findings=[_finding()],
        indices=(0,),
        source=None,
        boundary="FENCE_1",
        allowed_paths=frozenset({"pkg/api.py"}),
    )

    assert_that(rendered).does_not_contain("post-change")
    assert_that(rendered).contains("failure_scenario: Every call runs once.")


def test_cited_code_is_read_only_for_the_rounds_eligible_paths() -> None:
    """A finding on an unchanged file is sent without its code (#2734 review).

    Synthesis findings are not diff-gated before the pass, so a model-named
    path must not put an unchanged file's content in front of the provider.
    """
    reads: list[str] = []

    def _reader(path: str) -> str:
        reads.append(path)
        return "SECRET = 'do not send'\n"

    rendered = render_verification_findings(
        findings=[_finding(file="config/secrets.py", line=1)],
        indices=(0,),
        source=RepoContextSource(reader=_reader),
        boundary="FENCE_1",
        allowed_paths=frozenset({"pkg/api.py"}),
    )

    assert_that(reads).is_empty()
    assert_that(rendered).does_not_contain("do not send")
    assert_that(rendered).contains("file: config/secrets.py:1")


@pytest.mark.parametrize("spelling", ["./pkg/api.py", "pkg\\api.py", " pkg/api.py "])
def test_cited_code_matches_equivalent_path_spellings(spelling: str) -> None:
    """``./``, backslashes and stray whitespace still find the allowed path.

    Args:
        spelling: The model's spelling of ``pkg/api.py``.
    """
    reads: list[str] = []

    def _reader(path: str) -> str:
        reads.append(path)
        return "def send(payload, *, retries):\n    return retries\n"

    rendered = render_verification_findings(
        findings=[_finding(file=spelling)],
        indices=(0,),
        source=RepoContextSource(reader=_reader),
        boundary="FENCE_1",
        allowed_paths=frozenset({"pkg/api.py"}),
    )

    assert_that(reads).is_equal_to(["pkg/api.py"])
    assert_that(rendered).contains("post-change, around line 2")
    assert_that(rendered).contains("return retries")


# --- parsing ------------------------------------------------------------------


def test_parse_maps_labels_and_skips_malformed_entries() -> None:
    """Known labels map to outcomes; bad positions and labels are skipped."""
    content = json.dumps(
        {
            "verifications": [
                {"index": 1, "outcome": "Refuted", "evidence": " pkg/api.py:2 "},
                {"index": 2, "outcome": "weakened"},
                {"index": 3, "outcome": "unrefuted", "evidence": 7},
                {"index": 4, "outcome": "unrefuted"},
                {"index": True, "outcome": "unrefuted"},
                {"index": 2, "outcome": "unknown"},
                "junk",
            ],
        },
    )

    verdicts = parse_verification_answer(content=content, count=3)

    assert_that(verdicts).is_equal_to(
        {
            1: (VerificationOutcome.REFUTED, "pkg/api.py:2"),
            2: (VerificationOutcome.DOWNGRADED, ""),
            3: (VerificationOutcome.CONFIRMED, ""),
        },
    )


@pytest.mark.parametrize(
    "content",
    ["not json", "[]", json.dumps({"verifications": "no"}), "```json\n{}\n```"],
)
def test_parse_rejects_answers_outside_the_schema(content: str) -> None:
    """Anything but an object with a ``verifications`` list is no answer.

    Args:
        content: The off-schema answer.
    """
    assert_that(parse_verification_answer(content=content, count=1)).is_none()


@pytest.mark.parametrize(
    "evidence",
    ["not file evidence", "the retries kwarg is read on line 2", "pkg/api.py"],
)
def test_parse_drops_refutation_evidence_without_a_citation(evidence: str) -> None:
    """A refutation that cites no ``file:line`` carries no evidence.

    Args:
        evidence: Refutation text with no citation in it.
    """
    verdicts = parse_verification_answer(
        content=_answer((1, "refuted", evidence)),
        count=1,
    )

    assert_that(verdicts).is_equal_to({1: (VerificationOutcome.REFUTED, "")})


def test_parse_keeps_refutation_evidence_with_a_citation() -> None:
    """A ``file:line`` anywhere in the evidence keeps it."""
    verdicts = parse_verification_answer(
        content=_answer((1, "refuted", "see pkg/api.py:2, retries is read")),
        count=1,
    )

    assert_that(verdicts).is_equal_to(
        {1: (VerificationOutcome.REFUTED, "see pkg/api.py:2, retries is read")},
    )


def test_parse_keeps_the_first_verdict_for_a_repeated_position() -> None:
    """A duplicated position does not let a later entry overturn the first."""
    verdicts = parse_verification_answer(
        content=_answer((1, "unrefuted", ""), (1, "refuted", "x:1")),
        count=1,
    )

    assert_that(verdicts).is_equal_to({1: (VerificationOutcome.CONFIRMED, "")})


# --- application --------------------------------------------------------------


async def test_confirmed_findings_are_kept_and_marked_verified() -> None:
    """An unrefuted finding keeps its severity and gains the mark."""
    result, _call = await _pass(
        findings=[_finding()],
        content=_answer((1, "unrefuted", "")),
    )

    assert_that(result.findings[0].verified).is_true()
    assert_that(result.findings[0].severity).is_equal_to(Severity.P1)
    assert_that(result.summary.confirmed).is_equal_to(1)
    assert_that(result.summary.input_tokens).is_equal_to(100)


async def test_refuted_finding_is_dropped_and_recorded() -> None:
    """A refutation with evidence removes the finding and keeps the why."""
    result, _call = await _pass(
        findings=[_finding(), _finding(severity=Severity.P2, title="Kept")],
        content=_answer((1, "refuted", "pkg/api.py:2 retries is read below")),
    )

    assert_that([f.title for f in result.findings]).is_equal_to(["Kept"])
    assert_that(result.summary.refuted).is_equal_to(1)
    assert_that(result.summary.refutations).is_equal_to(
        (
            RefutedFinding(
                file="pkg/api.py",
                line=2,
                severity="P1",
                title="Retries never applied",
                evidence="pkg/api.py:2 retries is read below",
            ),
        ),
    )


@pytest.mark.parametrize("evidence", ["", "not file evidence"])
async def test_refutation_without_evidence_is_not_a_refutation(evidence: str) -> None:
    """The verifier's word alone never drops a finding; it counts as confirmed.

    Args:
        evidence: Empty, or prose with no ``file:line`` citation.
    """
    result, _call = await _pass(
        findings=[_finding()],
        content=_answer((1, "refuted", evidence)),
    )

    assert_that(result.findings).is_length(1)
    assert_that(result.findings[0].verified).is_true()
    assert_that(result.summary.refuted).is_equal_to(0)
    assert_that(result.summary.confirmed).is_equal_to(1)


async def test_refutation_citing_another_file_is_not_a_refutation() -> None:
    """A ``file:line`` into a file the verifier was not shown proves nothing."""
    result, _call = await _pass(
        findings=[_finding()],
        content=_answer((1, "refuted", "nonexistent.py:999 handles it")),
    )

    assert_that(result.findings).is_length(1)
    assert_that(result.findings[0].verified).is_true()
    assert_that(result.summary.refuted).is_equal_to(0)


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        ("pkg/api.py:2 retries is read", True),
        ("see ./pkg/api.py:2", True),
        ("pkg\\api.py:2", True),
        ("see api.py:2", False),
        ("src/pkg/api.py:2", False),
        ("other/api.py:2", False),
        ("nonexistent.py:999", False),
        ("pkg/api.py is fine", False),
        ("xpkg/api.py:2", False),
        ("foo-pkg/api.py:2", False),
        ("foo.pkg/api.py:2", False),
        ("../pkg/api.py:2", False),
        ("foo:pkg/api.py:2", False),
        ("@pkg/api.py:2", False),
        ("(pkg/api.py:2)", True),
        ("`pkg/api.py:2`, retries is read", True),
        ("pkg/api.py:2.", True),
        ("pkg/api.py:2-4 both branches", True),
        ('"pkg/api.py:2-4"', True),
    ],
)
def test_cites_finding_needs_the_findings_exact_path(
    evidence: str,
    expected: bool,
) -> None:
    """Only the finding's own path, normalized, counts as a citation.

    Args:
        evidence: Refutation text.
        expected: Whether it cites ``pkg/api.py``.
    """
    assert_that(cites_finding(evidence=evidence, file="pkg/api.py")).is_equal_to(
        expected,
    )


def test_cites_finding_normalizes_the_findings_own_spelling() -> None:
    """A finding path with whitespace or ``./`` still matches its citation."""
    assert_that(cites_finding(evidence="pkg/api.py:2", file=" ./pkg/api.py ")).is_true()
    assert_that(cites_finding(evidence="pkg/api.py:2", file="")).is_false()
    # Regex metacharacters in the path are matched literally.
    assert_that(
        cites_finding(evidence="see a+b/(c).py:3", file="a+b/(c).py"),
    ).is_true()
    # Quoting excludes only the active delimiter: parentheses, backticks and
    # the other quote are ordinary path characters inside it.
    assert_that(
        cites_finding(
            evidence='see "dir (legacy)/api.py:12"',
            file="dir (legacy)/api.py",
        ),
    ).is_true()
    assert_that(
        cites_finding(evidence="see `it's (v2)/api.py:3`", file="it's (v2)/api.py"),
    ).is_true()
    # The ruled shape: a spaced path with parentheses, wrapped whole in double
    # quotes; unquoted it is cut at the space.
    assert_that(
        cites_finding(evidence='fixed in "docs/a (b).md:3"', file="docs/a (b).md"),
    ).is_true()
    assert_that(
        cites_finding(evidence="fixed in docs/a (b).md:3", file="docs/a (b).md"),
    ).is_false()
    # A lone trailing ``)`` belongs to the path; only a matching pair is a wrapper.
    assert_that(cites_finding(evidence="see pkg/(x).py:3", file="pkg/(x).py")).is_true()
    # A path with a space must be quoted; unquoted it is cut at the space.
    assert_that(
        cites_finding(evidence='see "dir name/api.py:2" here', file="dir name/api.py"),
    ).is_true()
    assert_that(
        cites_finding(evidence="see dir name/api.py:2 here", file="dir name/api.py"),
    ).is_false()


async def test_weakened_with_a_citation_applies_to_a_padded_path() -> None:
    """The weakened check normalizes the finding's path like the queue does."""
    result, _call = await _pass(
        findings=[_finding(file=" pkg/api.py ")],
        content=_answer((1, "weakened", "pkg/api.py:2 only on retry")),
    )

    assert_that(result.findings[0].severity).is_equal_to(Severity.P2)
    assert_that(result.summary.downgraded).is_equal_to(1)


async def test_weakened_p1_becomes_p2_with_its_own_reason() -> None:
    """A weakened P1 moves to P2 tagged ``REFUTATION_WEAKENED`` and verified."""
    result, _call = await _pass(
        findings=[_finding()],
        content=_answer((1, "weakened", "pkg/api.py:2 only on retry")),
    )

    finding = result.findings[0]
    assert_that(finding.severity).is_equal_to(Severity.P2)
    assert_that(finding.severity_downgraded).is_true()
    assert_that(finding.severity_downgrade_reason).is_equal_to(
        SeverityDowngradeReason.REFUTATION_WEAKENED,
    )
    assert_that(finding.verified).is_true()
    assert_that(result.summary.downgraded).is_equal_to(1)


async def test_weakened_applies_only_to_a_p1() -> None:
    """A weakened low-confidence P2 stays P2; the verdict is confirmed."""
    result, _call = await _pass(
        findings=[_finding(severity=Severity.P2, confidence="low")],
        content=_answer((1, "weakened", "pkg/api.py:2")),
    )

    assert_that(result.findings[0].severity).is_equal_to(Severity.P2)
    assert_that(result.findings[0].severity_downgraded).is_false()
    assert_that(result.summary.downgraded).is_equal_to(0)
    assert_that(result.summary.confirmed).is_equal_to(1)


async def test_weakened_without_a_citation_is_confirmed() -> None:
    """Lowering a blocker takes the same ``file:line`` evidence as dropping one."""
    result, _call = await _pass(
        findings=[_finding()],
        content=_answer((1, "weakened", "")),
    )

    assert_that(result.findings[0].severity).is_equal_to(Severity.P1)
    assert_that(result.findings[0].verified).is_true()
    assert_that(result.summary.downgraded).is_equal_to(0)
    assert_that(result.summary.confirmed).is_equal_to(1)


async def test_unanswered_findings_are_counted_and_shown() -> None:
    """A partial answer is not a failure, but what it skipped is on the record."""
    result, _call = await _pass(
        findings=[_finding(), _finding(title="Second")],
        content=_answer((2, "unrefuted", "")),
    )

    assert_that(result.summary.failed).is_false()
    assert_that(result.summary.unanswered).is_equal_to(1)
    assert_that(result.summary.to_dict()["unanswered"]).is_equal_to(1)


async def test_a_stop_already_set_never_starts_the_call() -> None:
    """The provider coroutine is closed unstarted when the run is stopping."""
    stop = asyncio.Event()
    stop.set()
    result, call = await _pass(findings=[_finding()], stop=stop)

    call.assert_not_awaited()
    assert_that(result.summary.failed).is_true()


async def test_a_finding_without_a_verdict_stays_unverified() -> None:
    """A selected finding the answer skipped is kept, unmarked."""
    result, _call = await _pass(
        findings=[_finding(), _finding(title="Second")],
        content=_answer((2, "unrefuted", "")),
    )

    assert_that(result.findings[0].verified).is_false()
    assert_that(result.findings[1].verified).is_true()
    assert_that(result.summary.selected).is_equal_to(2)
    assert_that(result.summary.confirmed).is_equal_to(1)


async def test_unselected_findings_pass_through_untouched() -> None:
    """A high-confidence P2 is neither sent nor changed."""
    prompts: list[str] = []
    untouched = _finding(severity=Severity.P2, title="Untouched")
    result, _call = await _pass(
        findings=[untouched, _finding()],
        content=_answer((1, "unrefuted", "")),
        prompts=prompts,
    )

    assert_that(result.findings[0]).is_same_as(untouched)
    assert_that(prompts[0]).does_not_contain("Untouched")
    assert_that(prompts[0]).contains("Verify the 1 findings")


# --- fail-soft ----------------------------------------------------------------


async def test_nothing_selected_makes_no_call() -> None:
    """A round with no P1 and no low-confidence finding costs nothing."""
    result, call = await _pass(
        findings=[_finding(severity=Severity.P2)],
    )

    call.assert_not_awaited()
    assert_that(result.summary).is_equal_to(VerificationSummary(enabled=True))


async def test_off_mode_makes_no_call_and_reports_disabled() -> None:
    """``review.verify: off`` leaves the findings alone."""
    result, call = await _pass(findings=[_finding()], mode=ReviewVerifyMode.OFF)

    call.assert_not_awaited()
    assert_that(result.summary.enabled).is_false()


async def test_provider_failure_keeps_findings_unverified() -> None:
    """Any exception from the call is a failed pass, never a failed run."""
    findings = [_finding()]
    result, _call = await _pass(findings=findings, error=AIError("boom"))

    assert_that(result.findings).is_equal_to(tuple(findings))
    assert_that(result.summary.failed).is_true()
    assert_that(result.summary.selected).is_equal_to(1)
    assert_that(
        [d.reason for d in verification_degradations(summary=result.summary)],
    ).is_equal_to([CoverageDegradationReason.VERIFICATION_FAILED])


async def test_turn_limit_keeps_the_usage_it_spent() -> None:
    """A turn-limited call still charges its tokens to the run."""
    result, _call = await _pass(
        findings=[_finding()],
        error=AITurnLimitError(
            "limit",
            input_tokens=7,
            output_tokens=3,
            cost_estimate=0.5,
        ),
    )

    assert_that(result.summary.failed).is_true()
    assert_that(result.summary.input_tokens).is_equal_to(7)
    assert_that(result.summary.cost_estimate).is_equal_to(0.5)


async def test_off_schema_answer_is_a_failed_pass() -> None:
    """Prose instead of the JSON object fails soft with usage kept."""
    result, _call = await _pass(findings=[_finding()], content="I cannot.")

    assert_that(result.summary.failed).is_true()
    assert_that(result.summary.input_tokens).is_equal_to(100)
    assert_that(result.findings[0].verified).is_false()


async def test_an_answer_with_no_verdict_is_a_failed_pass() -> None:
    """``{"verifications": []}`` verifies nothing and must not read as success."""
    result, _call = await _pass(findings=[_finding()], content=_answer())

    assert_that(result.summary.failed).is_true()
    assert_that(result.summary.selected).is_equal_to(1)
    assert_that(result.findings[0].verified).is_false()
    assert_that(
        [d.reason for d in verification_degradations(summary=result.summary)],
    ).is_equal_to([CoverageDegradationReason.VERIFICATION_FAILED])


async def test_an_interrupt_abandons_the_call() -> None:
    """The stop event winning the race fails the pass soft, promptly."""
    stop = asyncio.Event()
    stop.set()

    result, _call = await asyncio.wait_for(
        _pass(findings=[_finding()], stop=stop),
        timeout=5,
    )

    assert_that(result.summary.failed).is_true()
    assert_that(
        [d.reason for d in verification_degradations(summary=result.summary)],
    ).is_equal_to([CoverageDegradationReason.VERIFICATION_FAILED])
    assert_that(verification_degradations(summary=None)).is_empty()


# --- the round: ordering, exemption, surfaces ---------------------------------


def _chunk_payload(*findings: dict[str, Any]) -> str:
    """Build a chunk response.

    Args:
        *findings: Raw finding mappings.

    Returns:
        JSON text for the chunk pass.
    """
    return json.dumps({"summary": "", "checklist": [], "findings": list(findings)})


def _raw_p1(**overrides: Any) -> dict[str, Any]:
    """Build a raw P1 finding as the model would report it.

    Args:
        **overrides: Keys to replace.

    Returns:
        The raw mapping.
    """
    raw: dict[str, Any] = {
        "severity": "P1",
        "category": "logic-bug",
        "file": "pkg/api.py",
        "line": 2,
        "title": "Retries never applied",
        "description": "d",
        "cause": "c",
        "fix": "f",
        "confidence": "high",
        "failure_scenario": "Every call runs once.",
        "evidence_style": "diff_local",
    }
    raw.update(overrides)
    return raw


def _run(
    *,
    chunk: str,
    verification: str | None = None,
    verify: ReviewVerifyMode = ReviewVerifyMode.P1_AND_LOW_CONFIDENCE,
    verification_prompts: list[str] | None = None,
) -> Any:
    """Run a one-chunk review with the verification call answered apart.

    Args:
        chunk: The chunk answer.
        verification: The verifier's answer; ``None`` answers "unrefuted"
            for every finding.
        verify: Verify mode for the session.
        verification_prompts: Optional sink for the verifier's user prompt.

    Returns:
        The review result.
    """
    provider = MagicMock()
    provider.aclose = AsyncMock()
    provider.model_name = "claude-sonnet-4-20250514"
    provider.name = "anthropic"
    provider.capabilities = ProviderCapabilities(supports_sessions=False)

    async def _call(
        *,
        user_prompt: str,
        system_prompt: str | None = None,
        **_kwargs: Any,
    ) -> AIResponse:
        if system_prompt == REVIEW_VERIFICATION_SYSTEM_PROMPT:
            if verification_prompts is not None:
                verification_prompts.append(user_prompt)
            count = int(user_prompt.split("Verify the ")[1].split(" ")[0])
            return _response(
                content=verification
                or _answer(*((i, "unrefuted", "") for i in range(1, count + 1))),
            )
        return _response(content=chunk)

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "lintro.ai.review.run_planning.resolve_review_chunks",
                return_value=[
                    ReviewChunk(
                        id=1,
                        files=["pkg/api.py"],
                        diff=_DIFF,
                        relationship=REL_SINGLE_FILE,
                    ),
                ],
            ),
        )
        stack.enter_context(
            patch("lintro.ai.review.provider_call.call_ai", side_effect=_call),
        )
        return run_review(
            _context(),
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(
                    enabled=True,
                    transport=AITransport.API,
                    max_parallel_calls=1,
                ),
                depth=1,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
                synthesis=None,
                verify=verify,
            ),
        )


def test_gates_run_after_verification_on_the_verified_severities() -> None:
    """A P1 the verifier weakened lands at P2 and the P1 gate stays quiet.

    The chunk pass parses ungated, so the verifier sees the model's own P1;
    the mechanical gates run last and read the P2 it produced.
    """
    result = _run(
        chunk=_chunk_payload(_raw_p1()),
        verification=_answer((1, "weakened", "pkg/api.py:2")),
    )

    finding = result.findings[0]
    assert_that(finding.severity).is_equal_to(Severity.P2)
    assert_that(finding.severity_downgrade_reason).is_equal_to(
        SeverityDowngradeReason.REFUTATION_WEAKENED,
    )
    assert_that(result.readiness_verdict).is_not_equal_to(ReviewVerdict.BLOCKED)
    assert_that(result.metadata.verification).is_not_none()
    assert_that(result.metadata.verification.downgraded).is_equal_to(1)
    phases = [span.name for span in result.metadata.timings.phases]
    assert_that(phases).contains(ReviewPhase.VERIFICATION)


def test_p1_gate_still_fires_after_verification() -> None:
    """A P1 without a failure scenario the verifier confirmed is still gated."""
    result = _run(chunk=_chunk_payload(_raw_p1(failure_scenario="")))

    finding = result.findings[0]
    assert_that(finding.verified).is_true()
    assert_that(finding.severity).is_equal_to(Severity.P2)
    assert_that(finding.severity_downgrade_reason).is_equal_to(
        SeverityDowngradeReason.P1_NO_FAILURE_SCENARIO,
    )


def test_refuted_p1_leaves_the_round_and_every_surface() -> None:
    """A refuted P1 is gone from the findings, the JSON, the note and the record."""
    result = _run(
        chunk=_chunk_payload(_raw_p1()),
        verification=_answer((1, "refuted", "pkg/api.py:2 retries is read")),
    )

    assert_that(result.findings).is_empty()
    assert_that(result.readiness_verdict).is_equal_to(ReviewVerdict.READY)
    payload = review_result_to_dict(result=result)
    assert_that(payload["verification"]["refuted"]).is_equal_to(1)
    assert_that(payload["verification"]["refutations"][0]["evidence"]).is_equal_to(
        "pkg/api.py:2 retries is read",
    )
    assert_that("verification").is_not_in(payload["metadata"])
    assert_that(format_verification_note(metadata=result.metadata)).is_equal_to(
        "Verification re-checked 1 finding: 1 refuted and dropped.",
    )
    record = run_record_from_result(
        request=StickyRequest(result=result, head_sha="abc", transport="api"),
        totals=RoundTotals(
            round_number=1,
            verdict=result.readiness_verdict,
            resolved=0,
            open_after=0,
            convergence_score=0.0,
        ),
    )
    assert_that(record.to_dict()["refuted"]).is_equal_to(1)
    assert_that("verified").is_not_in(record.to_dict())


def test_confirmed_round_counts_verified_findings_on_the_record() -> None:
    """The record and the JSON carry the verified mark and the pass's cost."""
    result = _run(chunk=_chunk_payload(_raw_p1()))

    payload = review_result_to_dict(result=result)
    assert_that(payload["findings"][0]["verified"]).is_true()
    assert_that(payload["verification"]["confirmed"]).is_equal_to(1)
    # One chunk call plus one verification call, each 100 / 50 tokens.
    assert_that(payload["metadata"]["token_usage"]["prompt"]).is_equal_to(200)
    record = run_record_from_result(
        request=StickyRequest(result=result, head_sha="abc", transport="api"),
        totals=RoundTotals(
            round_number=1,
            verdict=result.readiness_verdict,
            resolved=0,
            open_after=1,
            convergence_score=0.0,
        ),
    )
    assert_that(record.to_dict()["verified"]).is_equal_to(1)
    assert_that(format_verification_note_line(metadata=result.metadata)).is_equal_to(
        "<sub>Verification re-checked 1 finding: 1 confirmed.</sub>",
    )


def test_a_round_with_nothing_to_verify_renders_no_note() -> None:
    """A clean round reads exactly as it did before the pass existed."""
    result = _run(chunk=_chunk_payload())

    assert_that(result.metadata.verification).is_equal_to(
        VerificationSummary(enabled=True),
    )
    assert_that(format_verification_note(metadata=result.metadata)).is_empty()
    assert_that(review_result_to_dict(result=result)["verification"]).is_equal_to(
        VerificationSummary(enabled=True).to_dict(),
    )


def test_off_mode_skips_the_pass_for_the_round() -> None:
    """``off`` makes no verification call and the summary says disabled."""
    prompts: list[str] = []
    result = _run(
        chunk=_chunk_payload(_raw_p1()),
        verify=ReviewVerifyMode.OFF,
        verification_prompts=prompts,
    )

    assert_that(prompts).is_empty()
    assert_that(result.metadata.verification).is_not_none()
    assert_that(result.metadata.verification.enabled).is_false()
    assert_that(result.findings[0].verified).is_false()


def test_failed_pass_degrades_the_narrative_not_the_coverage() -> None:
    """A verifier that answered prose leaves coverage complete, note set."""
    result = _run(chunk=_chunk_payload(_raw_p1()), verification="no")

    assert_that(result.metadata.findings_coverage_complete).is_true()
    assert_that(result.metadata.synthesis_degraded).is_false()
    reasons = [d.reason for d in result.metadata.coverage_degradations]
    assert_that(reasons).contains(CoverageDegradationReason.VERIFICATION_FAILED)
    assert_that(format_verification_note(metadata=result.metadata)).is_equal_to(
        "Verification did not complete; 1 finding kept unverified.",
    )


def test_custom_agent_findings_are_exempt() -> None:
    """An author-declared severity is configuration; the verifier never sees it.

    The exemption keys on ``source``, not identity: by the time the finalizer
    runs, the synthesis pass's duplicate merge may have rewritten a custom
    finding (here: one carrying ``merged_duplicates``) or dropped one (the
    original ``custom_findings`` tuple still lists it). The round's custom
    subset is what survived, untouched, and the dropped one stays dropped.
    """
    original = _finding(title="Agent P1", failure_scenario="", source="agent")
    custom = replace(
        original,
        merged_duplicates=(
            MergedDuplicate(
                file="pkg/api.py",
                category="logic-bug",
                title="dup",
                line=9,
            ),
        ),
    )
    dropped = _finding(title="Agent dup", source="agent")
    outcome = ReviewRunOutcome(
        filtered_findings=(_finding(title="Model P1"), custom),
        custom_findings=(original, dropped),
    )
    plan = MagicMock()
    plan.ai_config = AIConfig(enabled=True, transport=AITransport.API)
    plan.budget = CostBudget(max_cost_usd=None)
    plan.repo_root = ""
    plan.resume.queue = ("pkg/api.py",)
    plan.resume.eligible = ("pkg/api.py",)
    prompts: list[str] = []

    async def _call(*, user_prompt: str, **_kwargs: Any) -> AIResponse:
        prompts.append(user_prompt)
        return _response(content=_answer((1, "unrefuted", "")))

    with patch("lintro.ai.review.provider_call.call_ai", side_effect=_call):
        gated = asyncio.run(
            _verify_and_gate(
                context=_context(),
                options=ReviewSessionOptions(
                    provider=MagicMock(),
                    ai_config=plan.ai_config,
                    depth=1,
                    checklist_items=[],
                    checklist_text="",
                    classifications=[],
                ),
                plan=plan,
                outcome=outcome,
                interrupt=asyncio.Event(),
            ),
        )

    assert_that(prompts[0]).does_not_contain("Agent P1")
    titles = [f.title for f in gated.filtered_findings]
    assert_that(titles).is_equal_to(["Model P1", "Agent P1"])
    # Same object, not a gated copy: the P1 gate would have moved it to P2.
    assert_that(gated.filtered_findings[1]).is_same_as(custom)
    assert_that(gated.custom_findings).is_equal_to((custom,))
    assert_that(gated.filtered_findings[0].verified).is_true()
    assert_that(gated.verification).is_equal_to(
        VerificationSummary(
            enabled=True,
            selected=1,
            confirmed=1,
            input_tokens=100,
            output_tokens=50,
            cost_estimate=0.01,
        ),
    )


def test_a_stopped_run_still_gates_its_built_in_findings() -> None:
    """The gates run on a stopped run too; only the verifier is skipped.

    Since #2728 the chunk pass parses ungated, so a run that stops at a cost
    cap after one chunk must gate that chunk's findings itself or an
    inflated P1 would reach the partial result and the posting tier.
    """
    custom = _finding(title="Agent P1", failure_scenario="", source="agent")
    outcome = ReviewRunOutcome(
        filtered_findings=(
            _finding(title="Unevidenced P1", failure_scenario=""),
            _finding(
                title="Unevidenced test gap",
                severity=Severity.P2,
                category="test-gap",
                evidence_claimed=False,
            ),
            custom,
        ),
        custom_findings=(custom,),
    )

    gated = gate_built_in_findings(outcome=outcome)

    by_title = {f.title: f for f in gated.filtered_findings}
    assert_that(by_title["Unevidenced P1"].severity).is_equal_to(Severity.P2)
    assert_that(by_title["Unevidenced P1"].severity_downgrade_reason).is_equal_to(
        SeverityDowngradeReason.P1_NO_FAILURE_SCENARIO,
    )
    assert_that(by_title["Unevidenced test gap"].severity).is_equal_to(Severity.P3)
    assert_that(by_title["Agent P1"]).is_same_as(custom)
    assert_that(gated.total_findings).is_equal_to(3)


def test_findings_off_the_queue_are_carried_past_the_verifier() -> None:
    """A finding on an unqueued path is neither sent nor gated (#2734 Codex).

    ``reject_context_findings`` converts it to a re-read flag later; it must
    not take a verifier slot or be refuted away before that.
    """
    off_queue = _finding(title="Elsewhere", file="other/mod.py", failure_scenario="")
    outcome = ReviewRunOutcome(
        filtered_findings=(_finding(title="Model P1"), off_queue),
        custom_findings=(),
    )
    plan = MagicMock()
    plan.ai_config = AIConfig(enabled=True, transport=AITransport.API)
    plan.budget = CostBudget(max_cost_usd=None)
    plan.repo_root = ""
    plan.resume.queue = ("pkg/api.py",)
    plan.resume.eligible = ("pkg/api.py", "other/mod.py")
    prompts: list[str] = []

    async def _call(*, user_prompt: str, **_kwargs: Any) -> AIResponse:
        prompts.append(user_prompt)
        return _response(content=_answer((1, "unrefuted", "")))

    with patch("lintro.ai.review.provider_call.call_ai", side_effect=_call):
        gated = asyncio.run(
            _verify_and_gate(
                context=_context(),
                options=ReviewSessionOptions(
                    provider=MagicMock(),
                    ai_config=plan.ai_config,
                    depth=1,
                    checklist_items=[],
                    checklist_text="",
                    classifications=[],
                ),
                plan=plan,
                outcome=outcome,
                interrupt=asyncio.Event(),
            ),
        )

    assert_that(prompts[0]).does_not_contain("Elsewhere")
    assert_that(prompts[0]).contains("Verify the 1 findings")
    by_title = {f.title: f for f in gated.filtered_findings}
    assert_that(by_title["Elsewhere"]).is_same_as(off_queue)
    assert_that(by_title["Model P1"].verified).is_true()


async def test_the_depth_3_sweep_parses_ungated_like_the_chunk_pass() -> None:
    """A sweep P1 without a failure scenario reaches the round as a P1.

    The gates run once per round after the verification pass; a P1 the
    adversarial sweep reported must not be lowered before the verifier sees
    it (#2734 Codex).
    """
    from lintro.ai.review.adversarial_pass import run_adversarial_pass

    response = _response(
        content=json.dumps(
            {
                "findings": [
                    {
                        **_raw_p1(failure_scenario=""),
                        "title": "Sweep P1",
                    },
                ],
            },
        ),
    )
    provider = MagicMock()
    provider.name = "anthropic"
    with patch(
        "lintro.ai.review.provider_call.call_ai",
        new=AsyncMock(return_value=response),
    ):
        sweep = await run_adversarial_pass(
            chunk=ReviewChunk(
                id=1,
                files=["pkg/api.py"],
                diff=_DIFF,
                relationship=REL_SINGLE_FILE,
            ),
            provider=provider,
            ai_config=AIConfig(enabled=True, transport=AITransport.API),
            prior_findings=(),
            budget=CostBudget(max_cost_usd=None),
            eligible_paths=frozenset({"pkg/api.py"}),
        )

    assert_that([f.title for f in sweep.findings]).is_equal_to(["Sweep P1"])
    assert_that(sweep.findings[0].severity).is_equal_to(Severity.P1)
    assert_that(sweep.findings[0].severity_downgraded).is_false()


# --- config -------------------------------------------------------------------


def test_verify_defaults_to_p1_and_low_confidence() -> None:
    """The default re-checks every P1 and every low-confidence finding."""
    assert_that(ReviewConfig().verify).is_equal_to(
        ReviewVerifyMode.P1_AND_LOW_CONFIDENCE,
    )


@pytest.mark.parametrize("value", ["off", "p1", "p1+low-confidence"])
def test_verify_accepts_each_documented_value(value: str) -> None:
    """Every documented mode parses from its config spelling.

    Args:
        value: The config string.
    """
    assert_that(ReviewConfig.model_validate({"verify": value}).verify).is_equal_to(
        ReviewVerifyMode(value),
    )


def test_verify_rejects_an_unknown_value() -> None:
    """A typo fails loudly at config time."""
    with pytest.raises(ValueError, match="verify"):
        ReviewConfig.model_validate({"verify": "p2"})
