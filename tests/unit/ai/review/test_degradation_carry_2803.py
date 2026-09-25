"""Degradations persisted in the review state and carried by a rerun (#2803)."""

from __future__ import annotations

import json
from pathlib import Path

from assertpy import assert_that

from lintro.ai.review.coverage_degradation import (
    GENERATED_QUESTIONS_FAILED_NOTE,
    describe_coverage_degradations,
)
from lintro.ai.review.degradation_carry import (
    RedoScope,
    carried_degradations,
    latest_degradations,
    redo_scope,
)
from lintro.ai.review.enums.coverage_degradation_reason import (
    NARRATIVE_DEGRADATION_REASONS,
    CoverageDegradationReason,
)
from lintro.ai.review.enums.degradation_step import DegradationStep, step_for_reason
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.github_constants import STATE_VERSION
from lintro.ai.review.github_notes import (
    format_coverage_limited_warning,
    format_partial_review_label,
    format_pass_note_lines,
)
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.coverage_degradation import (
    CARRIED_CHUNK_INDEX,
    SYNTHESIS_CHUNK_INDEX,
    CoverageDegradation,
)
from lintro.ai.review.models.coverage_record import CoverageRecord
from lintro.ai.review.models.degradation_record import DegradationRecord
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_coverage import RunCoverage
from lintro.ai.review.models.run_identity import RunIdentity
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.patch_hash import normalized_patch_hash
from lintro.ai.review.resume import plan_resume
from lintro.ai.review.run_record_factory import RoundTotals, run_record_from_result

_HEAD = "b0153e29169bfcb2b702b97ad92be5e4bf84929b"
_OTHER_HEAD = "0" * 40
_V5_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "fixtures"
    / "ai"
    / "review_state"
    / "v5_pr2826_final.json"
)
_Reason = CoverageDegradationReason


def _record(
    reason: CoverageDegradationReason,
    *,
    chunk_index: int = 0,
    paths: tuple[str, ...] = ("a.py",),
    head_sha: str = _HEAD,
) -> DegradationRecord:
    """Return a degradation record at ``head_sha``.

    Args:
        reason: The recorded reason.
        chunk_index: The chunk the reason hit in its round.
        paths: The chunk's files.
        head_sha: The head the round reviewed.

    Returns:
        The record.
    """
    return DegradationRecord.from_degradation(
        degradation=CoverageDegradation(
            reason=reason,
            chunk_index=chunk_index,
            paths=paths,
        ),
        head_sha=head_sha,
    )


def _metadata(
    *degradations: CoverageDegradation,
    chunks_total: int = 1,
    partial: bool = False,
) -> ReviewMetadata:
    """Return run metadata carrying ``degradations``.

    Args:
        *degradations: The run's coverage degradations.
        chunks_total: Chunks the run planned.
        partial: Whether the run stopped early.

    Returns:
        The metadata.
    """
    return ReviewMetadata(
        model="m",
        provider="anthropic",
        context_window=1,
        depth=1,
        chunks_total=chunks_total,
        chunks_current=0,
        files_reviewed=0,
        files_total=1,
        checklist_items=0,
        partial=partial,
        coverage_degradations=degradations,
    )


def _state(*records: DegradationRecord, sha: str = _HEAD) -> ReviewState:
    """Return a state whose one run reviewed ``sha`` with ``records``.

    Args:
        *records: The run's degradation records.
        sha: The head the run reviewed.

    Returns:
        The state.
    """
    return ReviewState(
        runs=(
            RunRecord(
                identity=RunIdentity(round=1, sha=sha),
                coverage=RunCoverage(degradations=records),
            ),
        ),
    )


def test_every_reason_has_a_step() -> None:
    """The step mapping is total, so no new reason can crash a state write."""
    for reason in CoverageDegradationReason:
        assert_that(step_for_reason(reason=reason)).is_instance_of(DegradationStep)


def test_a_failed_question_pass_is_narrative() -> None:
    """The question pass joins the reasons that never make a review partial."""
    assert_that(NARRATIVE_DEGRADATION_REASONS).contains(
        _Reason.GENERATED_QUESTIONS_FAILED,
    )
    metadata = _metadata(
        CoverageDegradation(
            reason=_Reason.GENERATED_QUESTIONS_FAILED,
            chunk_index=SYNTHESIS_CHUNK_INDEX,
            split=False,
        ),
    )

    assert_that(metadata.findings_coverage_complete).is_true()
    assert_that(describe_coverage_degradations(metadata=metadata)).is_empty()


def test_a_run_record_with_two_degradations_round_trips_unchanged() -> None:
    """Both records, paths, detail, limit and split survive the state blob."""
    records = (
        DegradationRecord.from_degradation(
            degradation=CoverageDegradation(
                reason=_Reason.GENERATED_QUESTIONS_FAILED,
                chunk_index=SYNTHESIS_CHUNK_INDEX,
                split=False,
                detail="not_json; retried once",
            ),
            head_sha=_HEAD,
        ),
        DegradationRecord.from_degradation(
            degradation=CoverageDegradation(
                reason=_Reason.TURN_LIMIT_REACHED,
                chunk_index=0,
                split=False,
                limit=12,
                paths=("a.py", "b.py"),
            ),
            head_sha=_HEAD,
        ),
    )
    run = RunRecord(
        identity=RunIdentity(round=1, sha=_HEAD),
        coverage=RunCoverage(degradations=records),
    )

    restored = RunRecord.from_dict(json.loads(json.dumps(run.to_dict())))

    assert_that(restored.coverage.degradations).is_equal_to(records)
    assert_that(restored.coverage.degradations[1].step).is_equal_to(
        DegradationStep.CHUNK,
    )


def test_a_record_without_degradations_writes_no_key() -> None:
    """A clean round's record is byte-identical to a v5 one."""
    payload = RunRecord(identity=RunIdentity(round=1, sha=_HEAD)).to_dict()

    assert_that(payload).does_not_contain_key("degradations")


def test_unknown_entries_are_dropped_not_fatal() -> None:
    """A reason or step from a newer lintro is skipped; the rest still load."""
    payload = RunRecord(identity=RunIdentity(round=1, sha=_HEAD)).to_dict()
    payload["degradations"] = [
        {"reason": "from_the_future", "step": "chunk", "chunk_index": 0},
        {"reason": "turn_limit_reached", "step": "later", "chunk_index": 0},
        "not a mapping",
        _record(_Reason.ADVERSARIAL_SWEEP_FAILED).to_dict(),
    ]

    restored = RunRecord.from_dict(payload)

    assert_that([item.reason for item in restored.coverage.degradations]).is_equal_to(
        [_Reason.ADVERSARIAL_SWEEP_FAILED],
    )


def test_a_real_v5_artifact_loads_with_no_degradations() -> None:
    """A captured v5 state (PR #2826's final artifact, redacted) still resumes.

    The fixture is the ``state.json`` the AI Review job uploaded for that PR,
    with finding text, flag reasons, the run narrative and the run id
    replaced. Everything the resume reads is as the job wrote it.
    """
    payload = json.loads(_V5_FIXTURE.read_text(encoding="utf-8"))
    assert_that(payload["schema_version"]).is_equal_to(5)

    state = ReviewState.from_artifact_dict(payload)

    assert_that(state.runs).is_length(1)
    assert_that(state.runs[0].identity.sha).is_equal_to(_HEAD)
    assert_that(state.runs[0].coverage.degradations).is_empty()
    assert_that(state.coverage).is_length(25)
    assert_that(state.pr_spend_usd).is_close_to(7.450541, 1e-9)
    assert_that(redo_scope(prior=state, head_sha=_HEAD)).is_equal_to(RedoScope())
    assert_that(state.to_artifact_dict()["schema_version"]).is_equal_to(
        STATE_VERSION,
    )


def test_only_the_latest_run_at_this_head_is_read() -> None:
    """An older head, or a latest run at another head, carries nothing."""
    record = _record(_Reason.ADVERSARIAL_SWEEP_FAILED)

    assert_that(latest_degradations(prior=_state(record), head_sha=_HEAD)).is_equal_to(
        (record,),
    )
    assert_that(
        latest_degradations(prior=_state(record, sha=_OTHER_HEAD), head_sha=_HEAD),
    ).is_empty()
    assert_that(latest_degradations(prior=None, head_sha=_HEAD)).is_empty()


def test_a_credited_per_file_reason_is_redone() -> None:
    """A split or swept chunk's files leave carried coverage for the rerun."""
    scope = redo_scope(
        prior=_state(
            _record(_Reason.OUTPUT_EXHAUSTION_RETRIED, paths=("a.py",)),
            _record(_Reason.ADVERSARIAL_SWEEP_FAILED, paths=("b.py",)),
        ),
        head_sha=_HEAD,
    )

    assert_that(scope).is_equal_to(RedoScope(paths=frozenset({"a.py", "b.py"})))


def test_narrative_and_cut_reasons_are_not_redone() -> None:
    """A warning is carried, not redone; a cut is re-reported by coverage."""
    scope = redo_scope(
        prior=_state(
            _record(_Reason.GENERATED_QUESTIONS_FAILED, paths=()),
            _record(_Reason.SYNTHESIS_FAILED, paths=()),
            _record(_Reason.DIFF_TRUNCATED),
        ),
        head_sha=_HEAD,
    )

    assert_that(scope).is_equal_to(RedoScope())


def test_a_per_file_reason_without_files_redoes_the_whole_head() -> None:
    """With no files to name, the redo fails toward more review."""
    scope = redo_scope(
        prior=_state(_record(_Reason.ADVERSARIAL_SWEEP_FAILED, paths=())),
        head_sha=_HEAD,
    )
    coverage = (CoverageRecord(path="a.py", patch_hash="h1"),)

    assert_that(scope.whole_head).is_true()
    assert_that(scope.filter_coverage(coverage=coverage, hashes={})).is_empty()


def test_the_redo_also_drops_a_same_hash_sibling() -> None:
    """A sibling's record at the redo file's hash cannot credit it back."""
    scope = RedoScope(paths=frozenset({"a.py"}))
    coverage = (
        CoverageRecord(path="a.py", patch_hash="same"),
        CoverageRecord(path="copy.py", patch_hash="same"),
        CoverageRecord(path="c.py", patch_hash="other"),
    )

    kept = scope.filter_coverage(coverage=coverage, hashes={"a.py": "same"})

    assert_that([record.path for record in kept]).is_equal_to(["c.py"])


def test_plan_resume_queues_the_redo_file_at_the_same_head() -> None:
    """A covered file whose last round degraded is queued again."""
    diffs = {
        path: (
            f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
            f"@@ -1,1 +1,2 @@\n context\n+change-{path}\n"
        )
        for path in ("a.py", "b.py")
    }
    context = ReviewContext(
        base_ref="main",
        head_ref=_HEAD,
        changed_files=[
            ChangedFile(path=path, status="modified", additions=1, deletions=0)
            for path in diffs
        ],
        unified_diff="\n".join(diffs.values()),
        pr_metadata=None,
    )
    coverage = tuple(
        CoverageRecord(path=path, patch_hash=normalized_patch_hash(diff))
        for path, diff in diffs.items()
    )
    degraded = _record(_Reason.ADVERSARIAL_SWEEP_FAILED, paths=("a.py",))
    prior = ReviewState(runs=_state(degraded).runs, coverage=coverage)

    assert_that(plan_resume(context=context, prior=prior).queue).is_equal_to(
        ("a.py",),
    )
    # The same state one push later carries both files: a new head starts fresh.
    moved = ReviewState(runs=_state(degraded, sha=_OTHER_HEAD).runs, coverage=coverage)
    assert_that(plan_resume(context=context, prior=moved).queue).is_empty()


def test_a_step_that_did_not_run_is_carried_with_its_warning() -> None:
    """A rerun with nothing to review records the failed question pass again."""
    record = _record(
        _Reason.GENERATED_QUESTIONS_FAILED,
        chunk_index=SYNTHESIS_CHUNK_INDEX,
        paths=(),
    )

    carried = carried_degradations(
        prior=_state(record),
        head_sha=_HEAD,
        current=(),
        reviewed=(),
        steps_ran=(),
    )

    assert_that(carried).is_equal_to((record.degradation,))


def test_a_step_that_ran_again_answers_for_itself() -> None:
    """A question pass this round ran replaces the earlier failure."""
    record = _record(
        _Reason.GENERATED_QUESTIONS_FAILED,
        chunk_index=SYNTHESIS_CHUNK_INDEX,
        paths=(),
    )

    carried = carried_degradations(
        prior=_state(record),
        head_sha=_HEAD,
        current=(),
        reviewed=("a.py",),
        steps_ran=(DegradationStep.QUESTION_PASS,),
    )

    assert_that(carried).is_empty()


def test_a_redone_file_drops_the_reason_and_an_unreviewed_one_keeps_it() -> None:
    """The per-file reason stands until its files are reviewed again."""
    record = _record(_Reason.ADVERSARIAL_SWEEP_FAILED, paths=("a.py", "b.py"))
    prior = _state(record)

    redone = carried_degradations(
        prior=prior,
        head_sha=_HEAD,
        current=(),
        reviewed=("a.py", "b.py"),
        steps_ran=(),
    )
    not_redone = carried_degradations(
        prior=prior,
        head_sha=_HEAD,
        current=(),
        reviewed=("a.py",),
        steps_ran=(),
    )

    assert_that(redone).is_empty()
    (row,) = not_redone
    assert_that(row.reason).is_equal_to(_Reason.ADVERSARIAL_SWEEP_FAILED)
    assert_that(row.chunk_index).is_equal_to(CARRIED_CHUNK_INDEX)
    assert_that(row.paths).is_equal_to(("a.py", "b.py"))


def test_recomputed_and_coverage_carried_reasons_are_not_carried() -> None:
    """No-tree is recomputed each round; a cut is re-reported by coverage."""
    carried = carried_degradations(
        prior=_state(
            _record(
                _Reason.NO_TREE_FOR_AGENT,
                chunk_index=CARRIED_CHUNK_INDEX,
                paths=(),
            ),
            _record(_Reason.DIFF_TRUNCATED),
        ),
        head_sha=_HEAD,
        current=(),
        reviewed=(),
        steps_ran=(),
    )

    assert_that(carried).is_empty()


def test_a_carried_per_file_reason_is_worded_as_not_redone() -> None:
    """The warning names the reason and never counts it as this round's chunk."""
    metadata = _metadata(
        CoverageDegradation(
            reason=_Reason.ADVERSARIAL_SWEEP_FAILED,
            chunk_index=CARRIED_CHUNK_INDEX,
            paths=("a.py",),
        ),
        chunks_total=0,
        partial=True,
    )

    note = describe_coverage_degradations(metadata=metadata)

    assert_that(metadata.findings_coverage_complete).is_false()
    assert_that(note).contains("was not redone (adversarial sweep failed)")
    assert_that(note).does_not_contain("of 0 chunk")
    assert_that(note).does_not_contain("Every chunk was reviewed")


def test_the_question_pass_note_is_fine_print_not_the_warning() -> None:
    """Posted surfaces show the note, never the coverage-limited warning."""
    metadata = _metadata(
        CoverageDegradation(
            reason=_Reason.GENERATED_QUESTIONS_FAILED,
            chunk_index=SYNTHESIS_CHUNK_INDEX,
            split=False,
        ),
    )

    assert_that(format_coverage_limited_warning(metadata=metadata)).is_empty()
    assert_that(format_partial_review_label(metadata=metadata)).is_empty()
    assert_that(format_pass_note_lines(metadata=metadata)).is_equal_to(
        ["", f"<sub>{GENERATED_QUESTIONS_FAILED_NOTE}</sub>"],
    )


def test_the_run_record_keeps_every_degradation_at_its_head() -> None:
    """The factory writes one record per degradation, stamped with the head."""
    degradations = (
        CoverageDegradation(
            reason=_Reason.GENERATED_QUESTIONS_FAILED,
            chunk_index=SYNTHESIS_CHUNK_INDEX,
            split=False,
        ),
        CoverageDegradation(
            reason=_Reason.ADVERSARIAL_SWEEP_FAILED,
            chunk_index=0,
            paths=("a.py",),
        ),
    )
    result = ReviewResult(metadata=_metadata(*degradations), summary="", findings=())

    coverage = run_record_from_result(
        request=StickyRequest(result=result, head_sha=_HEAD),
        totals=RoundTotals(
            round_number=1,
            verdict=ReviewVerdict.READY,
            resolved=0,
            open_after=0,
            convergence_score=0.0,
        ),
    ).coverage

    assert_that([record.degradation for record in coverage.degradations]).is_equal_to(
        list(degradations),
    )
    assert_that({record.head_sha for record in coverage.degradations}).is_equal_to(
        {_HEAD},
    )
    assert_that([record.step for record in coverage.degradations]).is_equal_to(
        [DegradationStep.QUESTION_PASS, DegradationStep.ADVERSARIAL_SWEEP],
    )
    assert_that(coverage.coverage_limited).is_true()


def test_a_reviewed_file_does_not_clear_a_question_pass_failure() -> None:
    """Only the question pass running again clears its failure."""
    record = _record(
        _Reason.GENERATED_QUESTIONS_FAILED,
        chunk_index=SYNTHESIS_CHUNK_INDEX,
        paths=(),
    )

    carried = carried_degradations(
        prior=_state(record),
        head_sha=_HEAD,
        current=(),
        reviewed=("a.py",),
        steps_ran=(),
    )

    assert_that(carried).is_equal_to((record.degradation,))


def test_a_redo_that_fails_again_reports_only_its_own_failure() -> None:
    """The fresh row stands; the earlier one is not carried beside it."""
    fresh = CoverageDegradation(
        reason=_Reason.TURN_LIMIT_REACHED,
        chunk_index=0,
        split=False,
        paths=("a.py",),
    )

    carried = carried_degradations(
        prior=_state(_record(_Reason.TURN_LIMIT_REACHED, paths=("a.py",))),
        head_sha=_HEAD,
        current=(fresh,),
        reviewed=(),
        steps_ran=(),
    )

    assert_that(carried).is_empty()
    note = describe_coverage_degradations(metadata=_metadata(fresh, *carried))
    assert_that(note).does_not_contain("not redone")
