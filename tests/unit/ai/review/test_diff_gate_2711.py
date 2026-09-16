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
    Hunk,
    hunks_from_diff,
)
from lintro.ai.review.finding_parser import parse_findings
from lintro.ai.review.merge import ChunkReviewPartial
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
    assert_that(a.hunks[1].changed_lines).is_empty()
    assert_that(hunks["src/b.py"].changed_lines).is_equal_to(frozenset({2}))


def test_lineless_sections_are_recorded_without_hunks() -> None:
    """A mode-only or binary section is known but can host no line."""
    diff = (
        "diff --git a/x b/x\nold mode 100644\nnew mode 100755\n"
        "diff --git a/logo.png b/logo.png\nBinary files a/logo.png and b/logo.png differ\n"
    )
    hunks = hunks_from_diff(diff=diff)
    assert_that(hunks).contains_key("x", "logo.png")
    assert_that(hunks["x"].hunks).is_empty()
    # A placeholder section with neither hunks nor markers is left alone.
    assert_that(hunks_from_diff(diff="diff --git a/p.py b/p.py\n+change")).is_empty()


def test_file_hunks_distance_and_nearest_changed_line() -> None:
    """Distance is to the nearest range edge; re-anchor prefers added lines."""
    hunk = Hunk(start=10, end=15, changed_lines=frozenset({11, 14}))
    hunks = FileHunks(hunks=(hunk,))
    assert_that(hunks.distance(12)).is_equal_to(0)
    assert_that(hunks.distance(8)).is_equal_to(2)
    assert_that(hunks.distance(18)).is_equal_to(3)
    assert_that(hunks.nearest_changed_line(8)).is_equal_to(11)
    assert_that(hunks.nearest_changed_line(18)).is_equal_to(14)
    deletion_only = FileHunks(hunks=(Hunk(start=42, end=42),))
    assert_that(deletion_only.nearest_changed_line(44)).is_equal_to(42)


def test_reanchoring_picks_the_nearest_hunk_before_its_changed_line() -> None:
    """Near a pure-deletion hunk the anchor is that hunk's start.

    An added line of a farther hunk is never chosen.
    """
    hunks = hunks_from_diff(diff=_DIFF)["src/a.py"]
    assert_that(hunks.nearest_changed_line(44)).is_equal_to(42)
    assert_that(hunks.nearest_changed_line(40)).is_equal_to(42)
    gate = _gate()
    (kept,) = gate.apply(findings=(_finding(line=44),))
    assert_that(kept.line).is_equal_to(42)


def test_a_truncated_hunk_body_bounds_the_range_to_the_lines_present() -> None:
    """A header claiming 100 lines with one present covers one line only."""
    diff = (
        "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
        "@@ -1,100 +1,100 @@\n+first\n"
    )
    (hunk,) = hunks_from_diff(diff=diff)["x.py"].hunks
    assert_that((hunk.start, hunk.end)).is_equal_to((1, 1))
    gate = DiffGate(hunks=hunks_from_diff(diff=diff), near_lines=0)
    assert_that(gate.apply(findings=(_finding(file="x.py", line=50),))).is_empty()


def test_a_hunk_cut_before_its_first_new_line_hosts_nothing() -> None:
    """Declared count 1 with no new-side line present is not a point range."""
    diff = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n"
    hunks = hunks_from_diff(diff=diff)
    assert_that(hunks).contains_key("x.py")
    assert_that(hunks["x.py"].hunks).is_empty()
    gate = DiffGate(hunks=hunks, near_lines=3)
    assert_that(gate.apply(findings=(_finding(file="x.py", line=1),))).is_empty()
    assert_that(gate.counts.outside_diff).is_equal_to(1)
    # A declared-zero hunk (pure deletion) is still a point.
    deletion = (
        "diff --git a/y.py b/y.py\n--- a/y.py\n+++ b/y.py\n@@ -3,2 +3,0 @@\n-a\n-b\n"
    )
    assert_that(hunks_from_diff(diff=deletion)["y.py"].ranges).is_equal_to(((3, 3),))


def test_quoted_paths_in_crlf_diffs_stay_inside_the_gate() -> None:
    """A quoted CRLF header must not split to nothing and free the file."""
    text = (
        'diff --git "a/x y.py" "b/x y.py"\r\n--- "a/x y.py"\r\n+++ "b/x y.py"\r\n'
        "@@ -1,2 +1,3 @@\r\n a\r\n+b\r\n c\r\n"
    )
    hunks = hunks_from_diff(diff=text)
    assert_that(hunks).contains_key("x y.py")
    assert_that(hunks["x y.py"].ranges).is_equal_to(((1, 3),))
    gate = DiffGate(hunks=hunks, near_lines=0)
    assert_that(gate.apply(findings=(_finding(file="x y.py", line=100),))).is_empty()
    lineless = (
        'diff --git "a/a b.png" "b/a b.png"\r\n'
        'Binary files "a/a b.png" and "b/a b.png" differ\r\n'
    )
    assert_that(hunks_from_diff(diff=lineless)["a b.png"].hunks).is_empty()


def test_crlf_lineless_sections_are_still_recognised() -> None:
    """A binary or mode-only marker followed by CRLF is not bypassed."""
    diff = (
        "diff --git a/logo.png b/logo.png\r\n"
        "Binary files a/logo.png and b/logo.png differ\r\n"
        "diff --git a/x b/x\r\nold mode 100644\r\nnew mode 100755\r\n"
    )
    hunks = hunks_from_diff(diff=diff)
    assert_that(hunks).contains_key("logo.png", "x")
    gate = DiffGate(hunks=hunks, near_lines=3)
    assert_that(gate.apply(findings=(_finding(file="logo.png", line=2),))).is_empty()


def test_a_path_with_two_sections_does_not_read_file_headers_as_lines() -> None:
    """The second section's ``+++`` header is never counted as an added line."""
    diff = (
        "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
        "@@ -1,2 +1,3 @@\n a\n+b\n c\n"
        "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
        "@@ -10,1 +11,2 @@\n d\n+e\n"
    )
    hunks = hunks_from_diff(diff=diff)["x.py"]
    assert_that(hunks.ranges).is_equal_to(((1, 3), (11, 12)))
    assert_that(hunks.changed_lines).is_equal_to(frozenset({2, 12}))


# --- classification -----------------------------------------------------------


def _gate(near_lines: int = DEFAULT_NEAR_LINES) -> DiffGate:
    diff = _DIFF + (
        "diff --git a/assets/logo.png b/assets/logo.png\n"
        "Binary files a/assets/logo.png and b/assets/logo.png differ\n"
    )
    return DiffGate(hunks=hunks_from_diff(diff=diff), near_lines=near_lines)


def test_a_binary_or_mode_only_file_cannot_host_a_line() -> None:
    """A binary or mode-only chunk file has no hunk, so a line on it is outside."""
    gate = _gate()
    assert_that(
        gate.apply(findings=(_finding(file="assets/logo.png", line=3),)),
    ).is_empty()
    assert_that(gate.counts.outside_diff).is_equal_to(1)
    # Line-less findings on it stay unanchored.
    kept = gate.apply(findings=(_finding(file="assets/logo.png", line=0),))
    assert_that(kept).is_length(1)


def test_reanchoring_drops_a_suggestion_written_for_the_old_line() -> None:
    """A suggestion replaces the line it was written for; moving it would misfire."""
    from dataclasses import replace

    from lintro.ai.review.enums.suggestion_drop_reason import SuggestionDropReason
    from lintro.ai.review.models.suggested_change import SuggestedChange

    gate = _gate()
    legacy = replace(_finding(line=8), suggested_code="x = 2")
    (kept,) = gate.apply(findings=(legacy,))
    assert_that(kept.line).is_equal_to(11)
    assert_that(kept.suggested_code).is_empty()
    assert_that(kept.suggestion_dropped).is_equal_to(SuggestionDropReason.REANCHORED)
    structured = replace(
        _finding(line=18),
        suggested_change=SuggestedChange(
            start_line=18,
            end_line=18,
            replacement="y = 1",
        ),
    )
    (kept,) = gate.apply(findings=(structured,))
    assert_that(kept.suggested_change).is_none()
    assert_that(kept.suggestion_dropped).is_equal_to(SuggestionDropReason.REANCHORED)
    # No suggestion: nothing to drop, nothing recorded.
    (plain,) = gate.apply(findings=(_finding(line=8),))
    assert_that(plain.suggestion_dropped).is_none()


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


async def test_the_depth_3_sweep_is_bounded_and_its_counts_fold_in() -> None:
    """Adversarial findings pass the same gate; counts reach the chunk partial."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from lintro.ai.review.adversarial_pass import run_adversarial_pass
    from lintro.ai.review.chunk_pass import _add_usage

    chunk = ReviewChunk(
        id=1,
        files=["src/a.py"],
        diff=_DIFF,
        relationship="directory-prefix",
    )
    response = AIResponse(
        content=(
            '{"findings": [{"severity": "P2", "file": "src/a.py", "line": 100, '
            '"title": "outside", "description": "d", "cause": "c", "fix": "f", '
            '"confidence": "high"}, {"severity": "P2", "file": "src/a.py", '
            '"line": 12, "title": "inside", "description": "d", "cause": "c", '
            '"fix": "f", "confidence": "high"}]}'
        ),
        model="m",
        provider=AIProvider.ANTHROPIC,
        input_tokens=1,
        output_tokens=1,
        cost_estimate=0.0,
    )
    provider = MagicMock()
    provider.name = "anthropic"
    budget = MagicMock()
    with patch(
        "lintro.ai.review.provider_call.call_ai",
        new=AsyncMock(return_value=response),
    ):
        sweep = await run_adversarial_pass(
            chunk=chunk,
            provider=provider,
            ai_config=AIConfig(enabled=True, review=True),
            prior_findings=(),
            budget=budget,
        )
    assert_that([f.title for f in sweep.findings]).is_equal_to(["inside"])
    assert_that(sweep.diff_gate.outside_diff).is_equal_to(1)
    main = ChunkReviewPartial(
        findings=(),
        input_tokens=0,
        output_tokens=0,
        cost_estimate=0.0,
        diff_gate=DiffGateCounts(reanchored=1),
    )
    assert_that(_add_usage(partial=main, extra=sweep).diff_gate).is_equal_to(
        DiffGateCounts(outside_diff=1, reanchored=1),
    )
