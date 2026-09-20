"""The P2 evidence gate (issue #2723, milestone 0 step 0.10).

Any open P2 flips the derived verdict from "nits only" to "changes requested",
and on the sampled PRs that flip came from test-gap and contract-drift
findings the model inferred rather than showed. The gate is mechanical: a P2
in one of those categories whose ``evidence_style`` is not ``diff_local`` is
moved to P3 at parse time, recorded on the finding with its reason, counted
on the run record beside the P1 gate's count, and named on every surface.
"""

from __future__ import annotations

from typing import Any

import pytest
from assertpy import assert_that

from lintro.ai.review.enums.evidence_style import EvidenceStyle
from lintro.ai.review.enums.finding_kind import FindingKind
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.enums.severity_downgrade_reason import SeverityDowngradeReason
from lintro.ai.review.finding_parser import parse_findings
from lintro.ai.review.github_notes import format_downgrade_note
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.run_outcome import RunOutcome
from lintro.ai.review.models.run_record import RunRecord
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
    ["logic-bug", "silent-failure", "security", "integration", "breaking-change"],
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


def test_questions_are_never_gated() -> None:
    """A question carries no severity semantics to gate."""
    (kept,) = apply_p2_evidence_gate(
        findings=[_finding(kind=FindingKind.QUESTION)],
    )

    assert_that(kept.severity_downgraded).is_false()


def test_a_p2_the_p1_gate_produced_is_not_gated_twice() -> None:
    """The P1 gate's choice is not the model's claim, so it stops at P2."""
    gated = apply_p2_evidence_gate(
        findings=apply_p1_evidence_gate(
            findings=[_finding(severity=Severity.P1, failure_scenario="")],
        ),
    )

    assert_that(gated[0].severity).is_equal_to(Severity.P2)
    assert_that(gated[0].severity_downgrade_reason).is_equal_to(
        SeverityDowngradeReason.P1_NO_FAILURE_SCENARIO,
    )


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
            _raw(severity="P1", category="test-gap", failure_scenario=""),
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
