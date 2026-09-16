"""Diff-bounded finding gate (#2711, lintro-ops milestone 0 step 0.7)."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers.response import AIResponse
from lintro.ai.review.diff_gate import (
    DEFAULT_NEAR_LINES,
    DiffGate,
    DiffGateCounts,
    FileHunks,
    hunks_from_diff,
)
from lintro.ai.review.finding_parser import parse_findings
from lintro.ai.review.models.finding_occurrence import FindingOccurrence
from lintro.ai.review.models.review_chunk import ReviewChunk
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.run_outcome import RunOutcome
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.response_pipeline import payload_to_partial

_DIFF = (
    "diff --git a/src/a.py b/src/a.py\n"
    "--- a/src/a.py\n"
    "+++ b/src/a.py\n"
    "@@ -10,4 +10,6 @@\n"
    " keep\n"
    "-old\n"
    "+new one\n"
    "+new two\n"
    " keep\n"
    "+new three\n"
    " keep\n"
    "@@ -40,2 +42,0 @@\n"
    "-gone\n"
    "-gone too\n"
    "diff --git a/src/b.py b/src/b.py\n"
    "--- a/src/b.py\n"
    "+++ b/src/b.py\n"
    "@@ -1,2 +1,3 @@\n"
    " x\n"
    "+y\n"
    " z\n"
)


def _finding(
    *,
    file: str = "src/a.py",
    line: int,
    occurrences: tuple[FindingOccurrence, ...] = (),
) -> ReviewFinding:
    return ReviewFinding(
        severity=Severity.P2,
        category="logic-bug",
        file=file,
        line=line,
        title=f"finding at {line}",
        description="d",
        cause="c",
        fix="f",
        confidence="high",
        occurrences=occurrences,
    )


# --- hunk parsing -------------------------------------------------------------


def test_hunks_carry_new_file_ranges_and_changed_lines() -> None:
    """Ranges are new-file spans; changed lines are the added lines' numbers."""
    hunks = hunks_from_diff(diff=_DIFF)
    assert_that(hunks).contains_key("src/a.py", "src/b.py")
    a = hunks["src/a.py"]
    # 10..15 for the first hunk; the pure deletion at +42,0 is a point.
    assert_that(a.ranges).is_equal_to(((10, 15), (42, 42)))
    assert_that(a.changed_lines).is_equal_to(frozenset({11, 12, 14}))
    assert_that(hunks["src/b.py"].changed_lines).is_equal_to(frozenset({2}))


def test_files_without_hunks_are_omitted() -> None:
    """A mode-only section has no hunk and therefore nothing to bound."""
    diff = "diff --git a/x b/x\nold mode 100644\nnew mode 100755\n"
    assert_that(hunks_from_diff(diff=diff)).is_empty()


def test_file_hunks_distance_and_nearest_changed_line() -> None:
    """Distance is to the nearest range edge; re-anchor prefers added lines."""
    hunks = FileHunks(ranges=((10, 15),), changed_lines=frozenset({11, 14}))
    assert_that(hunks.distance(12)).is_equal_to(0)
    assert_that(hunks.distance(8)).is_equal_to(2)
    assert_that(hunks.distance(18)).is_equal_to(3)
    assert_that(hunks.nearest_changed_line(8)).is_equal_to(11)
    assert_that(hunks.nearest_changed_line(18)).is_equal_to(14)
    deletion_only = FileHunks(ranges=((42, 42),), changed_lines=frozenset())
    assert_that(deletion_only.nearest_changed_line(44)).is_equal_to(42)


# --- classification -----------------------------------------------------------


def _gate(near_lines: int = DEFAULT_NEAR_LINES) -> DiffGate:
    return DiffGate(hunks=hunks_from_diff(diff=_DIFF), near_lines=near_lines)


def test_in_diff_findings_pass_untouched() -> None:
    """A line inside a hunk (context lines included) is kept as reported."""
    gate = _gate()
    kept = gate.apply(findings=(_finding(line=10), _finding(line=15)))
    assert_that([f.line for f in kept]).is_equal_to([10, 15])
    assert_that(gate.counts).is_equal_to(DiffGateCounts())


def test_near_findings_are_reanchored_to_the_nearest_changed_line() -> None:
    """Within the distance, the line moves onto an added line and is counted."""
    gate = _gate()
    kept = gate.apply(findings=(_finding(line=8), _finding(line=18)))
    assert_that([f.line for f in kept]).is_equal_to([11, 14])
    assert_that(gate.counts.reanchored).is_equal_to(2)
    assert_that(gate.counts.outside_diff).is_equal_to(0)


def test_outside_findings_are_dropped_and_counted() -> None:
    """Further than the distance from every hunk, the finding is dropped."""
    gate = _gate()
    kept = gate.apply(findings=(_finding(line=4), _finding(line=100)))
    assert_that(kept).is_empty()
    assert_that(gate.counts.outside_diff).is_equal_to(2)


def test_zero_distance_reanchors_nothing() -> None:
    """With the knob at 0 a near finding is outside."""
    gate = _gate(near_lines=0)
    assert_that(gate.apply(findings=(_finding(line=9),))).is_empty()
    assert_that(gate.counts.outside_diff).is_equal_to(1)


def test_line_less_findings_are_kept_as_unanchored() -> None:
    """A whole-file finding has nothing to bound and is kept, counted."""
    gate = _gate()
    kept = gate.apply(findings=(_finding(line=0),))
    assert_that(kept).is_length(1)
    assert_that(gate.counts.unanchored).is_equal_to(1)


def test_files_the_chunk_does_not_cover_are_left_to_the_path_gate() -> None:
    """Scope is not this gate's decision: an uncovered file passes through."""
    gate = _gate()
    kept = gate.apply(findings=(_finding(file="src/other.py", line=999),))
    assert_that(kept).is_length(1)
    assert_that(gate.counts).is_equal_to(DiffGateCounts())


def test_paths_are_normalized_like_the_path_gate() -> None:
    """A ``./`` prefix or backslashes do not hide the file from the gate."""
    gate = _gate()
    kept = gate.apply(findings=(_finding(file="./src\\a.py", line=100),))
    assert_that(kept).is_empty()


def test_occurrences_are_checked_one_by_one_and_the_primary_decides() -> None:
    """Outside occurrences are dropped, near ones re-anchored, the primary rules."""
    gate = _gate()
    finding = _finding(
        line=12,
        occurrences=(
            FindingOccurrence(file="src/a.py", line=100),  # outside: dropped
            FindingOccurrence(file="src/a.py", line=17),  # near: -> 14
            FindingOccurrence(file="src/b.py", line=2),  # in diff
            FindingOccurrence(file="src/b.py", line=0),  # unanchored: kept
        ),
    )
    (kept,) = gate.apply(findings=(finding,))
    assert_that([(o.file, o.line) for o in kept.occurrences]).is_equal_to(
        [("src/a.py", 14), ("src/b.py", 2), ("src/b.py", 0)],
    )
    assert_that(gate.counts.occurrences_dropped).is_equal_to(1)
    # A primary location outside drops the finding whatever its occurrences say.
    outside = _finding(
        line=100,
        occurrences=(FindingOccurrence(file="src/a.py", line=12),),
    )
    assert_that(gate.apply(findings=(outside,))).is_empty()


def test_counts_add_and_serialize() -> None:
    """Chunk counts fold into a run total; the mapping is plain ints."""
    total = DiffGateCounts(outside_diff=1, reanchored=2) + DiffGateCounts(
        outside_diff=3,
        unanchored=1,
        occurrences_dropped=4,
    )
    assert_that(total).is_equal_to(
        DiffGateCounts(
            outside_diff=4,
            reanchored=2,
            unanchored=1,
            occurrences_dropped=4,
        ),
    )
    assert_that(total.to_dict()).is_equal_to(
        {"outside_diff": 4, "reanchored": 2, "unanchored": 1, "occurrences_dropped": 4},
    )


# --- wiring -------------------------------------------------------------------


def test_parse_findings_runs_the_gate_before_the_p1_evidence_gate() -> None:
    """An outside P1 never reaches the evidence gate; a kept one still does."""
    raw = [
        {"severity": "P1", "file": "src/a.py", "line": 100, "title": "outside"},
        {"severity": "P1", "file": "src/a.py", "line": 12, "title": "inside"},
    ]
    gate = _gate()
    findings = parse_findings(raw_findings=raw, diff_gate=gate)
    assert_that([f.title for f in findings]).is_equal_to(["inside"])
    # No failure scenario: the evidence gate downgraded the survivor.
    assert_that(findings[0].severity).is_equal_to(Severity.P2)
    assert_that(findings[0].severity_downgraded).is_true()
    assert_that(gate.counts.outside_diff).is_equal_to(1)


def test_payload_to_partial_bounds_findings_to_the_chunk() -> None:
    """The chunk's diff bounds the payload and the counts ride the partial."""
    chunk = ReviewChunk(
        id=1,
        files=["src/a.py", "src/b.py"],
        diff=_DIFF,
        relationship="directory-prefix",
    )
    response = AIResponse(
        content="{}",
        model="m",
        provider=AIProvider.ANTHROPIC,
        input_tokens=1,
        output_tokens=1,
        cost_estimate=0.0,
    )
    payload = {
        "findings": [
            {"severity": "P2", "file": "src/a.py", "line": 100, "title": "outside"},
            {"severity": "P2", "file": "src/a.py", "line": 8, "title": "near"},
            {"severity": "P2", "file": "src/b.py", "line": 2, "title": "inside"},
        ],
    }
    partial = payload_to_partial(response=response, payload=payload, chunk=chunk)
    assert_that([(f.title, f.line) for f in partial.findings]).is_equal_to(
        [("near", 11), ("inside", 2)],
    )
    assert_that(partial.diff_gate).is_equal_to(
        DiffGateCounts(outside_diff=1, reanchored=1),
    )
    # Without a chunk nothing is bounded (synthesis and custom agents).
    unbounded = payload_to_partial(response=response, payload=payload)
    assert_that(unbounded.findings).is_length(3)
    assert_that(unbounded.diff_gate).is_equal_to(DiffGateCounts())


def test_the_knob_defaults_to_three_and_reaches_the_budget_view() -> None:
    """``ai.review_diff_gate_lines`` is the one configuration knob."""
    config = AIConfig()
    assert_that(config.review_diff_gate_lines).is_equal_to(DEFAULT_NEAR_LINES)
    assert_that(config.budget_config.review_diff_gate_lines).is_equal_to(3)
    assert_that(AIConfig(review_diff_gate_lines=0).review_diff_gate_lines).is_zero()


def test_the_run_record_carries_the_outside_count_only_when_non_zero() -> None:
    """Older records round-trip byte-identically; a drop is recorded."""
    assert_that(RunRecord().to_dict()).does_not_contain_key("dropped_outside_diff")
    record = RunRecord(outcome=RunOutcome(dropped_outside_diff=2))
    payload = record.to_dict()
    assert_that(payload).contains_entry({"dropped_outside_diff": 2})
    restored = RunRecord.from_dict(payload)
    assert_that(restored.outcome.dropped_outside_diff).is_equal_to(2)
