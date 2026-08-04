"""Tests for the P1 evidence gate and the #1925 finding-model fields."""

from __future__ import annotations

from typing import Any

import pytest
from assertpy import assert_that

from lintro.ai.review.enums.evidence_style import EvidenceStyle
from lintro.ai.review.enums.finding_kind import FindingKind
from lintro.ai.review.finding_parser import (
    normalize_evidence_style,
    normalize_finding_kind,
    parse_findings,
)
from lintro.ai.review.models.finding_occurrence import FindingOccurrence
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.severity_gate import (
    P1_DOWNGRADE_REASON,
    apply_p1_evidence_gate,
    count_downgrades,
    describe_downgrades,
    downgraded_findings,
)


def _raw(**overrides: Any) -> dict[str, Any]:
    """Build a raw model finding payload.

    Args:
        **overrides: Keys to set or replace on the base payload.

    Returns:
        The raw finding mapping.
    """
    payload: dict[str, Any] = {
        "severity": "P1",
        "category": "security",
        "file": "src/app.py",
        "line": 12,
        "title": "Auth bypass on unknown status",
        "description": "d",
        "cause": "c",
        "fix": "f",
        "confidence": "high",
    }
    payload.update(overrides)
    return payload


def _finding(**overrides: Any) -> ReviewFinding:
    """Build a review finding for gate tests.

    Args:
        **overrides: Fields to override on the base finding.

    Returns:
        The constructed finding.
    """
    fields: dict[str, Any] = {
        "severity": Severity.P1,
        "category": "security",
        "file": "src/app.py",
        "line": 12,
        "title": "Auth bypass on unknown status",
        "description": "d",
        "cause": "c",
        "fix": "f",
        "confidence": "high",
    }
    fields.update(overrides)
    return ReviewFinding(**fields)


def test_p1_with_failure_scenario_keeps_its_severity() -> None:
    """A P1 backed by a concrete failure mechanism is left alone."""
    findings = parse_findings(
        raw_findings=[_raw(failure_scenario="Expired tokens are treated as active")],
    )

    assert_that(findings).is_length(1)
    assert_that(findings[0].severity).is_equal_to(Severity.P1)
    assert_that(findings[0].severity_downgraded).is_false()


def test_p1_without_failure_scenario_is_downgraded_to_p2() -> None:
    """A P1 with no failure mechanism is downgraded rather than trusted."""
    findings = parse_findings(raw_findings=[_raw()])

    assert_that(findings[0].severity).is_equal_to(Severity.P2)
    assert_that(findings[0].severity_downgraded).is_true()


def test_p1_with_whitespace_only_failure_scenario_is_downgraded() -> None:
    """Whitespace does not satisfy the evidence gate."""
    findings = parse_findings(raw_findings=[_raw(failure_scenario="   \n  ")])

    assert_that(findings[0].severity).is_equal_to(Severity.P2)
    assert_that(findings[0].severity_downgraded).is_true()


@pytest.mark.parametrize(
    "severity",
    [Severity.P2, Severity.P3],
    ids=["severity=P2", "severity=P3"],
)
def test_gate_leaves_non_p1_severities_untouched(severity: Severity) -> None:
    """The gate only applies to P1; nothing else is rewritten.

    Args:
        severity: Non-blocking severity under test.
    """
    findings = apply_p1_evidence_gate(findings=(_finding(severity=severity),))

    assert_that(findings[0].severity).is_equal_to(severity)
    assert_that(findings[0].severity_downgraded).is_false()


def test_gate_never_downgrades_a_question() -> None:
    """Questions carry no severity semantics, so the gate skips them."""
    findings = apply_p1_evidence_gate(
        findings=(_finding(kind=FindingKind.QUESTION),),
    )

    assert_that(findings[0].severity_downgraded).is_false()


def test_severity_override_exempts_the_pass_from_the_gate() -> None:
    """An author-declared severity policy is not model output to calibrate."""
    findings = parse_findings(
        raw_findings=[_raw()],
        severity_override=Severity.P1,
    )

    assert_that(findings[0].severity).is_equal_to(Severity.P1)
    assert_that(findings[0].severity_downgraded).is_false()


def test_downgrade_is_visible_to_renderers() -> None:
    """A downgrade is exposed as a countable, describable record."""
    findings = parse_findings(
        raw_findings=[
            _raw(),
            _raw(title="Second unevidenced blocker", line=40),
            _raw(severity="P3", title="Nit"),
        ],
    )

    assert_that(count_downgrades(findings=findings)).is_equal_to(2)
    assert_that(downgraded_findings(findings=findings)).is_length(2)
    notice = describe_downgrades(findings=findings)
    assert_that(notice).contains("2 findings downgraded to P2")
    assert_that(notice).contains(P1_DOWNGRADE_REASON)


def test_downgrade_notice_is_singular_for_one_finding() -> None:
    """The rendered notice reads naturally for a single downgrade."""
    findings = parse_findings(raw_findings=[_raw()])

    assert_that(describe_downgrades(findings=findings)).is_equal_to(
        f"1 finding downgraded to P2: {P1_DOWNGRADE_REASON}",
    )


def test_downgrade_notice_is_empty_when_nothing_was_downgraded() -> None:
    """Surfaces render nothing when the gate found nothing to correct."""
    findings = parse_findings(
        raw_findings=[_raw(failure_scenario="Requests 500 under load")],
    )

    assert_that(describe_downgrades(findings=findings)).is_empty()


def test_parsed_finding_carries_the_new_model_fields() -> None:
    """kind, evidence style, and occurrences round-trip through the parser."""
    findings = parse_findings(
        raw_findings=[
            _raw(
                kind="question",
                evidence_style="speculative",
                occurrences=[
                    {"file": "src/app.py", "line": 12},
                    {"file": "src/other.py", "line": 30},
                ],
            ),
        ],
    )

    finding = findings[0]
    assert_that(finding.kind).is_equal_to(FindingKind.QUESTION)
    assert_that(finding.is_question).is_true()
    assert_that(finding.evidence_style).is_equal_to(EvidenceStyle.SPECULATIVE)
    assert_that(finding.occurrences).is_length(2)
    assert_that(finding.occurrences[1]).is_equal_to(
        FindingOccurrence(file="src/other.py", line=30),
    )


def test_missing_new_fields_degrade_to_defaults() -> None:
    """A model that ignores every #1925 field still produces a valid finding."""
    findings = parse_findings(raw_findings=[_raw(severity="P3")])

    finding = findings[0]
    assert_that(finding.kind).is_equal_to(FindingKind.FINDING)
    assert_that(finding.evidence_style).is_equal_to(EvidenceStyle.DIFF_LOCAL)
    assert_that(finding.failure_scenario).is_empty()
    assert_that(finding.occurrences).is_empty()
    assert_that(finding.all_occurrences).is_equal_to(
        (FindingOccurrence(file="src/app.py", line=12),),
    )


@pytest.mark.parametrize(
    "raw",
    ["banana", "", None, 7, {"nested": True}],
    ids=["unknown", "empty", "none", "number", "mapping"],
)
def test_malformed_kind_and_style_never_fail_the_run(raw: object) -> None:
    """Unusable values fall back to defaults instead of raising.

    Args:
        raw: Malformed value under test.
    """
    assert_that(normalize_finding_kind(raw=raw)).is_equal_to(FindingKind.FINDING)
    assert_that(normalize_evidence_style(raw=raw)).is_equal_to(
        EvidenceStyle.DIFF_LOCAL,
    )


@pytest.mark.parametrize(
    "occurrences",
    ["not-a-list", [], [{"line": 3}], ["nope", {"file": "  "}]],
    ids=["scalar", "empty", "no_file", "unusable_entries"],
)
def test_malformed_occurrences_degrade_to_the_findings_own_location(
    occurrences: object,
) -> None:
    """Unusable occurrence payloads leave the finding as a single location.

    Args:
        occurrences: Malformed ``occurrences`` value under test.
    """
    findings = parse_findings(
        raw_findings=[_raw(severity="P3", occurrences=occurrences)],
    )

    assert_that(findings[0].all_occurrences).is_length(1)


def test_run_record_round_trips_question_and_downgrade_counts() -> None:
    """Per-run severity distribution keeps inflation visible over time."""
    record = RunRecord(round=2, p1=1, p2=3, p3=4, questions=2, downgraded=5)

    restored = RunRecord.from_dict(record.to_dict())

    assert_that(restored.questions).is_equal_to(2)
    assert_that(restored.downgraded).is_equal_to(5)


def test_legacy_run_record_defaults_the_new_counts_to_zero() -> None:
    """A run recorded before #1925 parses cleanly with empty new counts."""
    restored = RunRecord.from_dict({"round": 1, "p1": 2})

    assert_that(restored.questions).is_equal_to(0)
    assert_that(restored.downgraded).is_equal_to(0)


@pytest.mark.parametrize(
    "failure_scenario",
    [None, False, 0, {"why": "because"}, ["reasons"]],
    ids=["none", "false", "zero", "mapping", "list"],
)
def test_a_non_string_failure_scenario_does_not_satisfy_the_gate(
    failure_scenario: object,
) -> None:
    """Malformed evidence is absent evidence.

    Regression guard: coercing the raw value with ``str()`` turns ``None``
    into the truthy literal ``"None"`` and walks an unevidenced P1 straight
    through the gate.

    Args:
        failure_scenario: Malformed ``failure_scenario`` value under test.
    """
    findings = parse_findings(
        raw_findings=[_raw(failure_scenario=failure_scenario)],
    )

    assert_that(findings[0].failure_scenario).is_empty()
    assert_that(findings[0].severity).is_equal_to(Severity.P2)
    assert_that(findings[0].severity_downgraded).is_true()


@pytest.mark.parametrize(
    "file",
    [None, 7, {"path": "a.py"}, ["a.py"]],
    ids=["none", "number", "mapping", "list"],
)
def test_a_non_string_occurrence_file_is_rejected(file: object) -> None:
    """A non-string path is dropped rather than coerced into a fake location.

    Args:
        file: Malformed occurrence ``file`` value under test.
    """
    findings = parse_findings(
        raw_findings=[_raw(severity="P3", occurrences=[{"file": file, "line": 4}])],
    )

    assert_that(findings[0].occurrences).is_empty()
