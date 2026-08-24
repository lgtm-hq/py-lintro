"""Tests for cross-run finding fingerprinting and matching (#1906)."""

from __future__ import annotations

from dataclasses import replace

import pytest
from assertpy import assert_that

from lintro.ai.review.enums.finding_kind import FindingKind
from lintro.ai.review.enums.finding_match_outcome import FindingMatchOutcome
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.finding_matcher import (
    FINGERPRINT_LENGTH,
    derive_verdict,
    fingerprint_for,
    match_findings,
    normalize_file_path,
    normalize_title,
)
from lintro.ai.review.models.finding_occurrence import (
    FindingOccurrence,
    parse_occurrences,
)
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_state import ReviewState


def _finding(
    *,
    title: str,
    file: str = "src/app.py",
    line: int = 10,
    category: str = "security",
    severity: Severity = Severity.P1,
) -> ReviewFinding:
    """Build a review finding for matcher tests.

    Args:
        title: Finding title.
        file: Repository-relative file path.
        line: Line number.
        category: Finding category label.
        severity: Finding severity.

    Returns:
        The constructed finding.
    """
    return ReviewFinding(
        severity=severity,
        category=category,
        file=file,
        line=line,
        title=title,
        description="d",
        cause="c",
        fix="f",
        confidence="high",
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Hardcoded  Credential", "hardcoded credential"),
        ("`token` leaks!", "token leaks"),
        ("Missing\tnull-check.", "missing null check"),
        ("   Spaced   Out   ", "spaced out"),
    ],
    ids=["whitespace-run", "backticks", "punctuation", "trim"],
)
def test_normalize_title_strips_noise(raw: str, expected: str) -> None:
    """Titles normalize to lowercase, punctuation-free, single-spaced text."""
    assert_that(normalize_title(raw)).is_equal_to(expected)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("./src/app.py", "src/app.py"),
        ("src\\app.py", "src/app.py"),
        (" src/app.py ", "src/app.py"),
        (" ./src/app.py ", "src/app.py"),
    ],
    ids=[
        "dot-prefix",
        "windows-separator",
        "surrounding-space",
        "space-then-dot-prefix",
    ],
)
def test_normalize_file_path(raw: str, expected: str) -> None:
    """File paths normalize to POSIX form without a leading ``./``."""
    assert_that(normalize_file_path(raw)).is_equal_to(expected)


def test_fingerprint_is_stable_across_line_drift() -> None:
    """The fingerprint ignores line numbers, which drift as the PR evolves."""
    first = fingerprint_for(file="src/app.py", category="security", title="Leak")
    second = fingerprint_for(file="./src/app.py", category="security", title="Leak")

    assert_that(first).is_equal_to(second)
    assert_that(first).is_length(FINGERPRINT_LENGTH)


def test_fingerprint_is_stable_across_title_punctuation() -> None:
    """Cosmetic punctuation and casing changes keep the fingerprint stable."""
    first = fingerprint_for(
        file="src/app.py",
        category="security",
        title="Hardcoded credential",
    )
    second = fingerprint_for(
        file="src/app.py",
        category="security",
        title="`Hardcoded`  Credential!",
    )

    assert_that(first).is_equal_to(second)


def test_fingerprint_differs_by_file_and_category() -> None:
    """Different files or categories produce different fingerprints."""
    base = fingerprint_for(file="src/a.py", category="security", title="Leak")
    other_file = fingerprint_for(file="src/b.py", category="security", title="Leak")
    other_category = fingerprint_for(file="src/a.py", category="perf", title="Leak")

    assert_that(base).is_not_equal_to(other_file)
    assert_that(base).is_not_equal_to(other_category)


def test_first_round_marks_every_finding_new() -> None:
    """With no prior state every finding is new and since_round is 1."""
    result = match_findings(
        previous=None,
        findings=[_finding(title="Leak"), _finding(title="Slow loop", line=30)],
        round_number=1,
        head_sha="abc123",
    )

    assert_that(result.new).is_length(2)
    assert_that(result.carried).is_empty()
    assert_that(result.resolved).is_empty()
    assert_that(result.regressed).is_empty()
    assert_that({record.since_round for record in result.records}).is_equal_to({1})


def test_repeat_finding_is_carried_with_original_since_round() -> None:
    """A finding reported again carries its original first-seen round."""
    first = match_findings(
        previous=None,
        findings=[_finding(title="Leak")],
        round_number=1,
    )
    state = ReviewState(runs=(), findings=first.records)

    second = match_findings(
        previous=state,
        findings=[_finding(title="Leak", line=42)],
        round_number=2,
        head_sha="sha2",
    )

    assert_that(second.carried).is_length(1)
    assert_that(second.new).is_empty()
    carried = second.carried[0]
    assert_that(carried.since_round).is_equal_to(1)
    assert_that(carried.line).is_equal_to(42)
    assert_that(second.outcome_for(record=carried)).is_equal_to(
        FindingMatchOutcome.CARRIED,
    )


def test_disappeared_finding_is_resolved_with_provenance() -> None:
    """A prior open finding absent this round is stamped resolved."""
    first = match_findings(
        previous=None,
        findings=[_finding(title="Leak")],
        round_number=1,
    )
    state = ReviewState(runs=(), findings=first.records)

    second = match_findings(
        previous=state,
        findings=[],
        round_number=2,
        head_sha="deadbeef",
    )

    assert_that(second.resolved).is_length(1)
    resolved = second.resolved[0]
    assert_that(resolved.status).is_equal_to(FindingStatus.RESOLVED)
    assert_that(resolved.resolved_sha).is_equal_to("deadbeef")
    assert_that(resolved.resolved_round).is_equal_to(2)
    assert_that(second.outcome_for(record=resolved)).is_equal_to(
        FindingMatchOutcome.RESOLVED,
    )


def test_reappearing_finding_is_marked_regressed() -> None:
    """A resolved finding reported again re-opens with a regressed marker."""
    first = match_findings(
        previous=None,
        findings=[_finding(title="Leak")],
        round_number=1,
    )
    second = match_findings(
        previous=ReviewState(findings=first.records),
        findings=[],
        round_number=2,
        head_sha="sha2",
    )
    third = match_findings(
        previous=ReviewState(findings=second.records),
        findings=[_finding(title="Leak")],
        round_number=3,
        head_sha="sha3",
    )

    assert_that(third.regressed).is_length(1)
    regressed = third.regressed[0]
    assert_that(regressed.regressed).is_true()
    assert_that(regressed.status).is_equal_to(FindingStatus.OPEN)
    assert_that(regressed.since_round).is_equal_to(1)
    # Prior resolution provenance is preserved for the link-back banner.
    assert_that(regressed.resolved_sha).is_equal_to("sha2")
    assert_that(third.outcome_for(record=regressed)).is_equal_to(
        FindingMatchOutcome.REGRESSED,
    )
    assert_that(third.new).is_empty()


def test_duplicate_fingerprints_get_ordinals_by_line_order() -> None:
    """Same-fingerprint findings are disambiguated by first-seen line order."""
    result = match_findings(
        previous=None,
        findings=[
            _finding(title="Hardcoded credential", line=90),
            _finding(title="Hardcoded credential", line=12),
        ],
        round_number=1,
    )

    by_line = {record.line: record.ordinal for record in result.records}
    assert_that(by_line).is_equal_to({12: 1, 90: 2})
    assert_that({record.key for record in result.records}).is_length(2)


def test_ambiguous_duplicates_match_by_nearest_line() -> None:
    """Later rounds pair ambiguous duplicates by nearest line distance."""
    first = match_findings(
        previous=None,
        findings=[
            _finding(title="Hardcoded credential", line=10),
            _finding(title="Hardcoded credential", line=100),
        ],
        round_number=1,
    )

    second = match_findings(
        previous=ReviewState(findings=first.records),
        findings=[
            _finding(title="Hardcoded credential", line=104),
            _finding(title="Hardcoded credential", line=12),
        ],
        round_number=2,
        head_sha="sha2",
    )

    assert_that(second.carried).is_length(2)
    assert_that(second.new).is_empty()
    assert_that(second.resolved).is_empty()


def test_shrinking_duplicate_group_resolves_only_the_extra() -> None:
    """When duplicates shrink, only the unmatched occurrence resolves."""
    first = match_findings(
        previous=None,
        findings=[
            _finding(title="Hardcoded credential", line=10),
            _finding(title="Hardcoded credential", line=100),
        ],
        round_number=1,
    )

    second = match_findings(
        previous=ReviewState(findings=first.records),
        findings=[_finding(title="Hardcoded credential", line=11)],
        round_number=2,
        head_sha="sha2",
    )

    assert_that(second.carried).is_length(1)
    assert_that(second.resolved).is_length(1)
    assert_that(second.resolved[0].line).is_equal_to(100)


def test_ambiguous_tie_prefers_carrying_over_an_open_finding() -> None:
    """An equidistant open/resolved tie carries the open finding over.

    A stale open finding is a lesser failure than a false "Addressed" banner,
    so the open prior record wins the pairing and nothing is resolved.
    """
    previous = ReviewState(
        findings=(
            FindingRecord(
                fingerprint=fingerprint_for(
                    file="src/app.py",
                    category="security",
                    title="Hardcoded credential",
                ),
                ordinal=1,
                severity=Severity.P1,
                category="security",
                title="Hardcoded credential",
                file="src/app.py",
                line=40,
                status=FindingStatus.RESOLVED,
                since_round=1,
                resolved_sha="sha1",
                resolved_round=1,
            ),
            FindingRecord(
                fingerprint=fingerprint_for(
                    file="src/app.py",
                    category="security",
                    title="Hardcoded credential",
                ),
                ordinal=2,
                severity=Severity.P1,
                category="security",
                title="Hardcoded credential",
                file="src/app.py",
                line=60,
                status=FindingStatus.OPEN,
                since_round=1,
            ),
        ),
    )

    result = match_findings(
        previous=previous,
        findings=[_finding(title="Hardcoded credential", line=50)],
        round_number=2,
        head_sha="sha2",
    )

    assert_that(result.carried).is_length(1)
    assert_that(result.regressed).is_empty()
    assert_that(result.resolved).is_empty()
    # The carried record keeps the open sibling's ordinal, so its key cannot
    # collide with the resolved record still tracked under ordinal 1.
    assert_that(result.carried[0].ordinal).is_equal_to(2)
    assert_that({record.key for record in result.records}).is_length(2)


def test_record_keys_stay_unique_across_rounds() -> None:
    """Identity keys never collide, even as duplicate groups grow and shrink."""
    first = match_findings(
        previous=None,
        findings=[
            _finding(title="Hardcoded credential", line=10),
            _finding(title="Hardcoded credential", line=100),
        ],
        round_number=1,
    )
    second = match_findings(
        previous=ReviewState(findings=first.records),
        findings=[_finding(title="Hardcoded credential", line=11)],
        round_number=2,
        head_sha="sha2",
    )
    third = match_findings(
        previous=ReviewState(findings=second.records),
        findings=[
            _finding(title="Hardcoded credential", line=11),
            _finding(title="Hardcoded credential", line=300),
            _finding(title="Hardcoded credential", line=500),
        ],
        round_number=3,
        head_sha="sha3",
    )

    keys = [record.key for record in third.records]
    assert_that(keys).is_length(len(set(keys)))
    assert_that(third.records).is_length(3)


def test_carried_finding_keeps_its_original_ordinal() -> None:
    """A carried finding's ordinal is stable even when siblings disappear."""
    first = match_findings(
        previous=None,
        findings=[
            _finding(title="Hardcoded credential", line=10),
            _finding(title="Hardcoded credential", line=100),
        ],
        round_number=1,
    )

    second = match_findings(
        previous=ReviewState(findings=first.records),
        findings=[_finding(title="Hardcoded credential", line=102)],
        round_number=2,
        head_sha="sha2",
    )

    assert_that(second.carried[0].ordinal).is_equal_to(2)


def test_resolved_records_are_retained_in_the_merged_set() -> None:
    """Already-resolved findings stay in state so history is not lost."""
    first = match_findings(
        previous=None,
        findings=[_finding(title="Leak")],
        round_number=1,
    )
    second = match_findings(
        previous=ReviewState(findings=first.records),
        findings=[],
        round_number=2,
        head_sha="sha2",
    )
    third = match_findings(
        previous=ReviewState(findings=second.records),
        findings=[_finding(title="Other", line=5)],
        round_number=3,
        head_sha="sha3",
    )

    assert_that(third.records).is_length(2)
    statuses = {record.status for record in third.records}
    assert_that(statuses).is_equal_to({FindingStatus.OPEN, FindingStatus.RESOLVED})


@pytest.mark.parametrize(
    ("severities", "expected"),
    [
        ((Severity.P1, Severity.P3), ReviewVerdict.BLOCKED),
        ((Severity.P2, Severity.P3), ReviewVerdict.CHANGES_REQUESTED),
        ((Severity.P3,), ReviewVerdict.NITS_ONLY),
        ((), ReviewVerdict.READY),
    ],
    ids=["blocked", "changes-requested", "nits-only", "ready"],
)
def test_derive_verdict_from_open_severities(
    severities: tuple[Severity, ...],
    expected: ReviewVerdict,
) -> None:
    """The readiness verdict follows the highest open severity."""
    result = match_findings(
        previous=None,
        findings=[
            _finding(title=f"Issue {index}", line=index, severity=severity)
            for index, severity in enumerate(severities, start=1)
        ],
        round_number=1,
    )

    assert_that(derive_verdict(findings=result.records)).is_equal_to(expected)


def test_derive_verdict_ignores_resolved_findings() -> None:
    """Resolved findings never hold a PR back."""
    first = match_findings(
        previous=None,
        findings=[_finding(title="Leak", severity=Severity.P1)],
        round_number=1,
    )
    second = match_findings(
        previous=ReviewState(findings=first.records),
        findings=[],
        round_number=2,
        head_sha="sha2",
    )

    assert_that(derive_verdict(findings=second.records)).is_equal_to(
        ReviewVerdict.READY,
    )


def _pattern(
    *,
    lines: tuple[int, ...],
    title: str = "Unchecked return value",
    file: str = "src/app.py",
) -> ReviewFinding:
    """Build a finding that occurs at several locations.

    Args:
        lines: Line numbers at which the pattern occurs.
        title: Finding title.
        file: Repository-relative file path of every occurrence.

    Returns:
        The constructed finding, anchored at the first occurrence.
    """
    return replace(
        _finding(title=title, file=file, line=lines[0], severity=Severity.P2),
        occurrences=tuple(FindingOccurrence(file=file, line=line) for line in lines),
    )


def test_a_single_occurrence_finding_tracks_one_location() -> None:
    """A finding with no explicit occurrences still counts as one (#1925)."""
    match = match_findings(
        previous=None,
        findings=[_finding(title="Solo defect")],
        round_number=1,
    )

    record = match.records[0]
    assert_that(record.occurrence_count).is_equal_to(1)
    assert_that(record.occurrence_total).is_equal_to(1)
    assert_that(record.occurrences_addressed).is_equal_to(0)


def test_a_repeated_pattern_is_one_tracked_finding() -> None:
    """Twenty call sites of one defect are one record, not twenty."""
    match = match_findings(
        previous=None,
        findings=[_pattern(lines=(10, 20, 30, 40))],
        round_number=1,
    )

    assert_that(match.records).is_length(1)
    assert_that(match.records[0].occurrence_total).is_equal_to(4)


def test_partial_progress_keeps_the_pattern_open_with_counts() -> None:
    """Fixing some occurrences is progress, not resolution."""
    first = match_findings(
        previous=None,
        findings=[_pattern(lines=(10, 20, 30, 40, 50))],
        round_number=1,
    )
    second = match_findings(
        previous=ReviewState(findings=first.records),
        findings=[_pattern(lines=(30, 50))],
        round_number=2,
        head_sha="deadbeef",
    )

    assert_that(second.resolved).is_empty()
    assert_that(second.carried).is_length(1)
    record = second.records[0]
    assert_that(record.status).is_equal_to(FindingStatus.OPEN)
    assert_that(record.occurrence_count).is_equal_to(2)
    assert_that(record.occurrence_total).is_equal_to(5)
    assert_that(record.occurrences_addressed).is_equal_to(3)


def test_a_pattern_resolves_only_when_every_occurrence_is_gone() -> None:
    """Pattern-level resolution needs the whole pattern to disappear."""
    first = match_findings(
        previous=None,
        findings=[_pattern(lines=(10, 20, 30))],
        round_number=1,
    )
    second = match_findings(
        previous=ReviewState(findings=first.records),
        findings=[],
        round_number=2,
        head_sha="cafebabe",
    )

    record = second.records[0]
    assert_that(record.status).is_equal_to(FindingStatus.RESOLVED)
    assert_that(record.occurrences_addressed).is_equal_to(3)


def test_one_reappearing_occurrence_regresses_the_whole_pattern() -> None:
    """A single returning call site reopens the pattern with its full total."""
    first = match_findings(
        previous=None,
        findings=[_pattern(lines=(10, 20, 30))],
        round_number=1,
    )
    resolved = match_findings(
        previous=ReviewState(findings=first.records),
        findings=[],
        round_number=2,
        head_sha="cafebabe",
    )
    third = match_findings(
        previous=ReviewState(findings=resolved.records),
        findings=[_pattern(lines=(20,))],
        round_number=3,
        head_sha="f00d",
    )

    assert_that(third.regressed).is_length(1)
    record = third.records[0]
    assert_that(record.status).is_equal_to(FindingStatus.OPEN)
    assert_that(record.regressed).is_true()
    assert_that(record.occurrence_count).is_equal_to(1)
    assert_that(record.occurrence_total).is_equal_to(3)
    assert_that(record.occurrences_addressed).is_equal_to(2)


def test_questions_are_tracked_but_excluded_from_the_verdict() -> None:
    """A tracked question never blocks the PR (#1925)."""
    question = replace(
        _finding(title="Is this intentional", severity=Severity.P1),
        kind=FindingKind.QUESTION,
    )
    match = match_findings(previous=None, findings=[question], round_number=1)

    assert_that(match.records).is_length(1)
    assert_that(match.records[0].is_question).is_true()
    assert_that(derive_verdict(findings=match.records)).is_equal_to(
        ReviewVerdict.READY,
    )


def test_occurrences_survive_a_state_round_trip() -> None:
    """Occurrence counts persist in the state blob, not just in memory."""
    match = match_findings(
        previous=None,
        findings=[_pattern(lines=(10, 20, 30))],
        round_number=1,
    )

    restored = FindingRecord.from_dict(match.records[0].to_dict())

    assert_that(restored).is_not_none()
    assert restored is not None  # narrow type for mypy
    assert_that(restored.occurrence_total).is_equal_to(3)
    assert_that(restored.occurrences).is_length(3)


def test_a_pre_1925_record_degrades_to_a_single_occurrence() -> None:
    """State written before #1925 parses with sane occurrence defaults."""
    restored = FindingRecord.from_dict(
        {"fingerprint": "abc123", "severity": "P2", "file": "a.py", "line": 3},
    )

    assert_that(restored).is_not_none()
    assert restored is not None  # narrow type for mypy
    assert_that(restored.kind).is_equal_to(FindingKind.FINDING)
    assert_that(restored.occurrence_count).is_equal_to(1)
    assert_that(restored.occurrence_total).is_equal_to(1)


def test_an_omitted_occurrence_list_is_silence_not_progress() -> None:
    """A round that reports no occurrences inherits the tracked locations.

    Regression guard: synthesizing a single anchor occurrence for a finding
    that simply omitted the field would read as "2 of 3 addressed" on every
    degraded round, inventing progress that never happened.
    """
    first = match_findings(
        previous=None,
        findings=[_pattern(lines=(10, 20, 30))],
        round_number=1,
    )
    second = match_findings(
        previous=ReviewState(findings=first.records),
        findings=[
            _finding(title="Unchecked return value", line=10, severity=Severity.P2),
        ],
        round_number=2,
        head_sha="deadbeef",
    )

    record = second.records[0]
    assert_that(record.occurrence_count).is_equal_to(3)
    assert_that(record.occurrence_total).is_equal_to(3)
    assert_that(record.occurrences_addressed).is_equal_to(0)


def test_a_lone_occurrence_away_from_the_anchor_survives_serialization() -> None:
    """A single occurrence at another location is real state, not noise."""
    record = FindingRecord(
        fingerprint="abc123",
        file="src/app.py",
        line=10,
        occurrences=(FindingOccurrence(file="src/other.py", line=99),),
    )

    restored = FindingRecord.from_dict(record.to_dict())

    assert_that(restored).is_not_none()
    assert restored is not None  # narrow type for mypy
    assert_that(restored.occurrences).is_equal_to(
        (FindingOccurrence(file="src/other.py", line=99),),
    )


def test_parse_occurrences_deduplicates_repeated_locations() -> None:
    """A model that reports the same location twice yields one occurrence.

    The matcher counts occurrences by tuple length to derive
    ``occurrences_total`` and partial-progress counts. A duplicate raw
    location must not inflate that count, or a later round that reports the
    same location once would look like progress was made when the location
    never changed.
    """
    parsed = parse_occurrences(
        [
            {"file": "src/app.py", "line": 10},
            {"file": "src/app.py", "line": 10},
            {"file": "src/app.py", "line": 20},
        ],
    )

    assert_that(parsed).is_equal_to(
        (
            FindingOccurrence(file="src/app.py", line=10),
            FindingOccurrence(file="src/app.py", line=20),
        ),
    )


def test_unread_file_findings_carry_forward() -> None:
    """Absence because a file was not re-reviewed is not a fix."""
    first = match_findings(
        previous=None,
        findings=[_finding(title="Leak", file="src/app.py")],
        round_number=1,
    )
    second = match_findings(
        previous=ReviewState(findings=first.records),
        findings=[],
        round_number=2,
        head_sha="sha2",
        reviewed_paths=frozenset({"src/other.py"}),
    )

    assert_that(second.resolved).is_empty()
    assert_that(second.carried).is_length(1)
    assert_that(second.carried[0].status).is_equal_to(FindingStatus.OPEN)


def test_departed_path_resolves_unread_finding() -> None:
    """A deleted file's findings resolve even though it was not re-read."""
    first = match_findings(
        previous=None,
        findings=[_finding(title="Leak", file="src/gone.py")],
        round_number=1,
    )
    second = match_findings(
        previous=ReviewState(findings=first.records),
        findings=[],
        round_number=2,
        head_sha="sha2",
        reviewed_paths=frozenset(),
        departed_paths=frozenset({"src/gone.py"}),
    )

    assert_that(second.resolved).is_length(1)
    assert_that(second.resolved[0].resolved_sha).is_equal_to("sha2")
    assert_that(second.resolved[0].status).is_equal_to(FindingStatus.RESOLVED)
