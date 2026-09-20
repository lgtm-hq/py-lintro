"""The P2 evidence gate (issue #2723, milestone 0 step 0.10).

Any open P2 flips the derived verdict from "nits only" to "changes requested",
and on the sampled PRs that flip came from test-gap and contract-drift
findings the model inferred rather than showed. The gate is mechanical: a P2
in one of those categories whose ``evidence_style`` is not ``diff_local`` is
moved to P3 at parse time, recorded on the finding with its reason, counted
on the run record beside the P1 gate's count, and named on every surface.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from assertpy import assert_that

from lintro.ai.review.enums.evidence_style import EvidenceStyle
from lintro.ai.review.enums.finding_kind import FindingKind
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.review_strictness import ReviewStrictness
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.enums.severity_downgrade_reason import SeverityDowngradeReason
from lintro.ai.review.finding_parser import parse_findings
from lintro.ai.review.github_notes import format_downgrade_note
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.run_outcome import RunOutcome
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.sensitivity import (
    filter_findings_by_policy,
    resolve_sensitivity_policy,
)
from lintro.ai.review.severity_gate import (
    P1_DOWNGRADE_REASON,
    P2_DOWNGRADE_REASON,
    P2_EVIDENCE_GATED_CATEGORIES,
    apply_p1_evidence_gate,
    apply_p2_evidence_gate,
    count_downgrades,
    count_downgrades_by_reason,
    describe_downgrades,
)
from lintro.ai.review.sticky import (
    advance_review_state,
    build_sticky_comment,
    render_state_sticky,
)
from lintro.ai.review.verdict import derive_readiness_verdict
from lintro.enums.review_category import ReviewCategory


def _finding(**overrides: Any) -> ReviewFinding:
    """Build a P2 test-gap finding traced outside the hunk.

    Args:
        **overrides: Fields to override on the base finding.

    Returns:
        The constructed finding.
    """
    fields: dict[str, Any] = {
        "severity": Severity.P2,
        "category": "test-gap",
        "file": "src/app.py",
        "line": 12,
        "title": "New branch has no test",
        "description": "d",
        "cause": "c",
        "fix": "f",
        "confidence": "high",
        "evidence_style": EvidenceStyle.CROSS_FILE,
    }
    fields.update(overrides)
    return ReviewFinding(**fields)


def _raw(**overrides: Any) -> dict[str, Any]:
    """Build a raw model payload for the same finding.

    Args:
        **overrides: Keys to set or replace on the base payload.

    Returns:
        The raw finding mapping.
    """
    payload: dict[str, Any] = {
        "severity": "P2",
        "category": "test-gap",
        "file": "src/app.py",
        "line": 12,
        "title": "New branch has no test",
        "description": "d",
        "cause": "c",
        "fix": "f",
        "confidence": "high",
        "evidence_style": "cross_file",
    }
    payload.update(overrides)
    return payload


# --- the gate ----------------------------------------------------------------


def test_the_gated_categories_are_exactly_the_three_named() -> None:
    """The gate reaches test-gap, contract-drift and code-smell, nothing else."""
    assert_that(P2_EVIDENCE_GATED_CATEGORIES).is_equal_to(
        {"test-gap", "contract-drift", "code-smell"},
    )
    for category in ReviewCategory:
        assert_that(str(category) in P2_EVIDENCE_GATED_CATEGORIES).is_equal_to(
            category
            in {
                ReviewCategory.TEST_GAP,
                ReviewCategory.CONTRACT_DRIFT,
                ReviewCategory.CODE_SMELL,
            },
        )


@pytest.mark.parametrize("category", sorted(P2_EVIDENCE_GATED_CATEGORIES))
@pytest.mark.parametrize(
    "style",
    [EvidenceStyle.CROSS_FILE, EvidenceStyle.SPECULATIVE],
)
def test_an_unevidenced_p2_in_a_gated_category_moves_to_p3(
    category: str,
    style: EvidenceStyle,
) -> None:
    """The verdict-flipping claim without diff-local evidence becomes a nit.

    Args:
        category: The gated category under test.
        style: A non-diff-local evidence style.
    """
    (gated,) = apply_p2_evidence_gate(
        findings=[_finding(category=category, evidence_style=style)],
    )

    assert_that(gated.severity).is_equal_to(Severity.P3)
    assert_that(gated.severity_downgraded).is_true()
    assert_that(gated.severity_downgrade_reason).is_equal_to(
        SeverityDowngradeReason.P2_UNEVIDENCED,
    )


@pytest.mark.parametrize("category", sorted(P2_EVIDENCE_GATED_CATEGORIES))
def test_a_diff_local_p2_in_a_gated_category_is_kept(category: str) -> None:
    """Shown in the hunk itself, the claim keeps the model's severity.

    Args:
        category: The gated category under test.
    """
    (kept,) = apply_p2_evidence_gate(
        findings=[_finding(category=category, evidence_style=EvidenceStyle.DIFF_LOCAL)],
    )

    assert_that(kept.severity).is_equal_to(Severity.P2)
    assert_that(kept.severity_downgraded).is_false()
    assert_that(kept.severity_downgrade_reason).is_none()


@pytest.mark.parametrize(
    "category",
    sorted(
        str(item)
        for item in ReviewCategory
        if str(item) not in P2_EVIDENCE_GATED_CATEGORIES
    ),
)
def test_behaviour_categories_are_never_gated(category: str) -> None:
    """A cross-file trace is a legitimate way to show incorrect behaviour.

    Args:
        category: A category outside the gate.
    """
    (kept,) = apply_p2_evidence_gate(
        findings=[
            _finding(category=category, evidence_style=EvidenceStyle.SPECULATIVE),
        ],
    )

    assert_that(kept.severity).is_equal_to(Severity.P2)
    assert_that(kept.severity_downgraded).is_false()


@pytest.mark.parametrize("severity", [Severity.P1, Severity.P3])
def test_only_p2_findings_are_gated(severity: Severity) -> None:
    """P1 has its own gate; P3 has no lower band.

    Args:
        severity: A severity the P2 gate ignores.
    """
    (kept,) = apply_p2_evidence_gate(findings=[_finding(severity=severity)])

    assert_that(kept.severity).is_equal_to(severity)
    assert_that(kept.severity_downgraded).is_false()


@pytest.mark.parametrize(
    "raw",
    ["", "unknown", None, 42],
    ids=["absent", "bogus", "null", "int"],
)
def test_an_unstated_or_unreadable_style_is_not_evidence(raw: object) -> None:
    """The gate fails closed: a claim the model did not make is no claim.

    The normalizer maps such labels to ``diff_local`` for display and the
    convergence score; the gate reads the label as written.

    Args:
        raw: The payload's ``evidence_style`` value.
    """
    payload = _raw()
    if raw is None:
        del payload["evidence_style"]
    else:
        payload["evidence_style"] = raw

    (gated,) = parse_findings(raw_findings=[payload])

    assert_that(gated.evidence_style).is_equal_to(EvidenceStyle.DIFF_LOCAL)
    assert_that(gated.severity).is_equal_to(Severity.P3)
    assert_that(gated.severity_downgrade_reason).is_equal_to(
        SeverityDowngradeReason.P2_UNEVIDENCED,
    )


def test_the_gate_needs_one_claim_per_finding() -> None:
    """A misaligned claims list is a programming error, not a silent skip."""
    with pytest.raises(ValueError, match="one entry per finding"):
        apply_p2_evidence_gate(findings=[_finding()], claimed_styles=[])


def test_questions_are_never_gated() -> None:
    """A question carries no severity semantics to gate."""
    (kept,) = apply_p2_evidence_gate(
        findings=[_finding(kind=FindingKind.QUESTION)],
    )

    assert_that(kept.severity_downgraded).is_false()


def test_an_inflated_p1_in_a_gated_category_ends_at_p3() -> None:
    """The gates chain: over-claiming P1 lands where the honest P2 lands."""
    gated = apply_p2_evidence_gate(
        findings=apply_p1_evidence_gate(
            findings=[_finding(severity=Severity.P1, failure_scenario="")],
        ),
    )

    assert_that(gated[0].severity).is_equal_to(Severity.P3)
    assert_that(gated[0].severity_downgrade_reason).is_equal_to(
        SeverityDowngradeReason.P2_UNEVIDENCED,
    )


def test_an_inflated_p1_outside_the_gated_categories_stops_at_p2() -> None:
    """The P1 gate alone applies to a behaviour category."""
    gated = apply_p2_evidence_gate(
        findings=apply_p1_evidence_gate(
            findings=[
                _finding(
                    severity=Severity.P1,
                    category="security",
                    failure_scenario="",
                ),
            ],
        ),
    )

    assert_that(gated[0].severity).is_equal_to(Severity.P2)
    assert_that(gated[0].severity_downgrade_reason).is_equal_to(
        SeverityDowngradeReason.P1_NO_FAILURE_SCENARIO,
    )


@pytest.mark.parametrize(
    "raw",
    ["TEST-GAP", "test_gap", "  test gap ", "Contract_Drift", "code smell"],
)
def test_category_spelling_cannot_evade_the_gate(raw: str) -> None:
    """A recognized category is canonicalized at parse time.

    Args:
        raw: A non-canonical spelling of a gated category.
    """
    (gated,) = parse_findings(raw_findings=[_raw(category=raw)])

    assert_that(gated.category).is_in("test-gap", "contract-drift", "code-smell")
    assert_that(gated.severity).is_equal_to(Severity.P3)


def test_an_unrecognized_category_is_kept_and_never_gated() -> None:
    """A custom agent's category survives as written and is outside the gate."""
    (kept,) = parse_findings(raw_findings=[_raw(category="my-agent-rule")])

    assert_that(kept.category).is_equal_to("my-agent-rule")
    assert_that(kept.severity).is_equal_to(Severity.P2)


def test_gate_lowered_findings_survive_the_focused_preset() -> None:
    """A downgrade never turns into a drop: the note keeps its finding."""
    policy = resolve_sensitivity_policy(strictness=ReviewStrictness.FOCUSED)
    lowered = apply_p2_evidence_gate(findings=[_finding(category="contract-drift")])
    own = (_finding(severity=Severity.P3, category="contract-drift"),)

    assert_that(filter_findings_by_policy(findings=lowered, policy=policy)).is_length(1)
    assert_that(filter_findings_by_policy(findings=own, policy=policy)).is_empty()


def test_order_and_every_other_field_are_preserved() -> None:
    """The gate rewrites severity and its marker and nothing else."""
    first = _finding(title="first")
    second = _finding(title="second", evidence_style=EvidenceStyle.DIFF_LOCAL)

    gated = apply_p2_evidence_gate(findings=[first, second])

    assert_that([item.title for item in gated]).is_equal_to(["first", "second"])
    assert_that(gated[0].description).is_equal_to(first.description)
    assert_that(gated[0].evidence_style).is_equal_to(EvidenceStyle.CROSS_FILE)
    assert_that(gated[1]).is_equal_to(second)


# --- parse-time wiring and the verdict ---------------------------------------


def test_parse_findings_runs_the_p2_gate_after_the_p1_gate() -> None:
    """Both gates apply at parse time, in order."""
    findings = parse_findings(
        raw_findings=[
            _raw(),
            _raw(severity="P1", category="security", failure_scenario=""),
        ],
    )

    assert_that([item.severity for item in findings]).is_equal_to(
        [Severity.P3, Severity.P2],
    )
    assert_that([item.severity_downgrade_reason for item in findings]).is_equal_to(
        [
            SeverityDowngradeReason.P2_UNEVIDENCED,
            SeverityDowngradeReason.P1_NO_FAILURE_SCENARIO,
        ],
    )


def test_an_author_declared_severity_is_never_gated() -> None:
    """A custom agent's front-matter severity is configuration, not a claim."""
    (kept,) = parse_findings(raw_findings=[_raw()], severity_override=Severity.P2)

    assert_that(kept.severity).is_equal_to(Severity.P2)
    assert_that(kept.severity_downgraded).is_false()


def test_the_verdict_reads_the_gated_severity() -> None:
    """The one unevidenced test-gap P2 no longer flips the run to changes requested."""
    findings = parse_findings(raw_findings=[_raw()])

    assert_that(derive_readiness_verdict(findings=findings)).is_equal_to(
        ReviewVerdict.NITS_ONLY,
    )
    assert_that(
        derive_readiness_verdict(
            findings=parse_findings(raw_findings=[_raw(evidence_style="diff_local")]),
        ),
    ).is_equal_to(ReviewVerdict.CHANGES_REQUESTED)


# --- counts, record, surfaces ------------------------------------------------


def _mixed() -> tuple[ReviewFinding, ...]:
    """Return one finding from each gate plus an ungated one.

    Returns:
        Three findings after both gates.
    """
    return apply_p2_evidence_gate(
        findings=apply_p1_evidence_gate(
            findings=[
                _finding(
                    severity=Severity.P1,
                    category="security",
                    failure_scenario="",
                ),
                _finding(),
                _finding(evidence_style=EvidenceStyle.DIFF_LOCAL),
            ],
        ),
    )


def test_downgrades_are_counted_per_reason() -> None:
    """The P1 count keeps its meaning; the P2 gate has its own."""
    findings = _mixed()

    assert_that(count_downgrades_by_reason(findings=findings)).is_equal_to(
        {
            SeverityDowngradeReason.P1_NO_FAILURE_SCENARIO: 1,
            SeverityDowngradeReason.P2_UNEVIDENCED: 1,
        },
    )
    assert_that(count_downgrades(findings=findings)).is_equal_to(1)


def test_a_record_without_a_reason_counts_as_the_p1_gate() -> None:
    """States written before #2723 only had the one gate."""
    counts = count_downgrades_by_reason(
        findings=[_finding(severity_downgraded=True, severity_downgrade_reason=None)],
    )

    assert_that(counts[SeverityDowngradeReason.P1_NO_FAILURE_SCENARIO]).is_equal_to(1)
    assert_that(counts[SeverityDowngradeReason.P2_UNEVIDENCED]).is_equal_to(0)


def test_the_notice_names_each_gate_and_only_the_ones_that_fired() -> None:
    """Surfaces state every gate-driven rewrite, never a silent one."""
    both = describe_downgrades(findings=_mixed())
    p2_only = describe_downgrades(
        findings=apply_p2_evidence_gate(findings=[_finding()]),
    )

    assert_that(both).is_equal_to(
        f"1 finding downgraded to P2: {P1_DOWNGRADE_REASON}; "
        f"1 finding downgraded to P3: {P2_DOWNGRADE_REASON}",
    )
    assert_that(p2_only).is_equal_to(
        f"1 finding downgraded to P3: {P2_DOWNGRADE_REASON}",
    )
    assert_that(
        describe_downgrades(
            findings=[_finding(evidence_style=EvidenceStyle.DIFF_LOCAL)],
        ),
    ).is_empty()

    note = format_downgrade_note(findings=_mixed())
    assert_that(note).starts_with("> 🎚️ **")
    assert_that(note).contains(P2_DOWNGRADE_REASON, "not the model")
    assert_that(format_downgrade_note(findings=())).is_empty()


def test_the_run_record_carries_the_p2_count_only_when_set() -> None:
    """``downgraded_p2`` is an optional key so older readers see no new key."""
    quiet = RunRecord(outcome=RunOutcome(downgraded=1)).to_dict()
    loud = RunRecord(outcome=RunOutcome(downgraded=1, downgraded_p2=2)).to_dict()

    assert_that(quiet).does_not_contain_key("downgraded_p2")
    assert_that(quiet["downgraded"]).is_equal_to(1)
    assert_that(loud["downgraded_p2"]).is_equal_to(2)
    assert_that(RunRecord.from_dict(loud).outcome.downgraded_p2).is_equal_to(2)
    assert_that(RunRecord.from_dict(quiet).outcome.downgraded_p2).is_equal_to(0)


def test_the_finding_record_round_trips_the_reason() -> None:
    """The state ledger keeps the reason so a replayed finding is not gated twice."""
    gated = _mixed()[1]
    record = FindingRecord(
        fingerprint="fp",
        ordinal=1,
        severity=gated.severity,
        category=gated.category,
        title=gated.title,
        file=gated.file,
        line=gated.line,
        status=FindingStatus.OPEN,
        since_round=1,
        severity_downgraded=gated.severity_downgraded,
        severity_downgrade_reason=gated.severity_downgrade_reason,
    )

    payload = record.to_dict()
    assert_that(payload["severity_downgrade_reason"]).is_equal_to("p2_unevidenced")
    restored = FindingRecord.from_dict(payload)
    assert restored is not None
    assert_that(restored.severity_downgrade_reason).is_equal_to(
        SeverityDowngradeReason.P2_UNEVIDENCED,
    )
    bogus = FindingRecord.from_dict({**payload, "severity_downgrade_reason": "bogus"})
    assert bogus is not None
    assert_that(bogus.severity_downgrade_reason).is_none()
    legacy = FindingRecord.from_dict(
        {k: v for k, v in payload.items() if k != "severity_downgrade_reason"},
    )
    assert legacy is not None
    assert_that(legacy.severity_downgrade_reason).is_none()


# --- sticky surfaces ---------------------------------------------------------


def test_both_sticky_renders_carry_the_downgrade_note(
    sample_review_result: ReviewResult,
) -> None:
    """The round sticky and the state-derived re-render both name the gate.

    A converged or errored round re-renders the sticky from the ledger with
    no round result, so the note there comes from the persisted reasons.

    Args:
        sample_review_result: Shared review result fixture.
    """
    gated = apply_p2_evidence_gate(findings=[_finding()])
    result = replace(
        sample_review_result,
        findings=(*sample_review_result.findings, *gated),
    )
    request = StickyRequest(
        result=result,
        head_sha="a" * 40,
        transport="cli",
        auth_mode="subscription",
    )

    round_body = build_sticky_comment(request=request)
    state_body = render_state_sticky(state=advance_review_state(request=request))

    for body in (round_body, state_body):
        assert_that(body).contains("🎚️", P2_DOWNGRADE_REASON)
