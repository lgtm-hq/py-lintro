"""Delta rounds (#2627): what a round reads, and what it must never skip.

The scratch repository has ``main`` at A, a PR branch A → B → C, a ``main``
that moves to M under the PR (touching a file the PR does not), and a
rewritten branch A → B' (a force-push).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404 - scratch repositories are driven with fixed git argv
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

from lintro.ai.review.context.diff_parse import split_unified_diff_by_file
from lintro.ai.review.coverage import ClassifyFilesRequest, classify_files, queue_paths
from lintro.ai.review.delta import (
    apply_delta_hunks,
    delta_hunks,
    open_thread_paths,
    plan_delta,
)
from lintro.ai.review.enums.delta_reason import DeltaReason
from lintro.ai.review.enums.file_review_need import FileReviewNeed
from lintro.ai.review.enums.finding_match_outcome import FindingMatchOutcome
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.review_checkout import ReviewCheckout
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.finding_identity import fingerprint_for
from lintro.ai.review.finding_matcher import match_findings
from lintro.ai.review.group_labels import REL_SINGLE_FILE
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.coverage_record import CoverageRecord
from lintro.ai.review.models.delta_plan import DeltaPlan
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.pr_metadata import PRMetadata
from lintro.ai.review.models.review_chunk import ReviewChunk
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_coverage import RunCoverage
from lintro.ai.review.models.run_identity import RunIdentity
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.output import review_result_to_dict
from lintro.ai.review.sticky.assembly import render_state_sticky
from lintro.ai.review.sticky.scope import _scope_line
from lintro.ai.review.sticky.state import matcher_reviewed_ranges, stamp_finding_ids
from lintro.ai.review.verdict import apply_coverage_gate, derive_readiness_verdict

pytestmark = pytest.mark.verification

_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull,
}


def _git(cwd: Path, *args: str) -> str:
    """Run git in a scratch repository.

    Args:
        cwd: The repository.
        *args: Git arguments.

    Returns:
        Stripped stdout.
    """
    return subprocess.run(  # nosec B603 B607 - fixed argv over a scratch repository
        [shutil.which("git") or "git", *args],
        cwd=cwd,
        env={**os.environ, **_ENV},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.fixture
def scratch(tmp_path: Path) -> dict[str, Any]:
    """A repository with the shapes a delta round meets.

    Args:
        tmp_path: Pytest temp dir.

    Returns:
        Paths and commit ids: ``a`` (base), ``b``, ``c`` (PR head after two
        pushes), ``m`` (main moved on, touching ``other.py``), ``b_prime``
        (the PR branch rewritten from ``a``).
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "api.py").write_text("def send(payload):\n    return 1\n")
    (repo / "other.py").write_text("x = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "A")
    a = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "pr")
    (repo / "api.py").write_text("def send(payload, *, retries):\n    return 1\n")
    _git(repo, "commit", "-q", "-am", "B")
    b = _git(repo, "rev-parse", "HEAD")
    (repo / "api.py").write_text("def send(payload, *, retries):\n    return retries\n")
    (repo / "new.py").write_text("y = 2\n")
    _git(repo, "add", "new.py")
    _git(repo, "commit", "-q", "-am", "C")
    c = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    (repo / "other.py").write_text("x = 2\n")
    _git(repo, "commit", "-q", "-am", "M")
    m = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "pr-rewritten", a)
    (repo / "api.py").write_text("def send(payload, retries=0):\n    return 1\n")
    _git(repo, "commit", "-q", "-am", "B'")
    b_prime = _git(repo, "rev-parse", "HEAD")
    return {"repo": repo, "a": a, "b": b, "c": c, "m": m, "b_prime": b_prime}


def _state(*, sha: str, **coverage: Any) -> ReviewState:
    """One prior round at ``sha``.

    Args:
        sha: The prior round's head.
        **coverage: Extra ``ReviewState`` fields.

    Returns:
        The state.
    """
    return ReviewState(
        runs=(RunRecord(identity=RunIdentity(round=1, sha=sha)),),
        **coverage,
    )


# --- the decision -------------------------------------------------------------


def _ctx(
    *,
    head: str,
    base: str,
    pr: bool = True,
    checkout: ReviewCheckout = ReviewCheckout.HEAD,
) -> ReviewContext:
    """A collected context for the scratch PR.

    Args:
        head: The head under review.
        base: The base branch tip ``gh`` would report.
        pr: Whether this is a ``--pr`` run (carries PR metadata).
        checkout: What tree the run has.

    Returns:
        The context.
    """
    return ReviewContext(
        base_ref=base,
        head_ref=head,
        changed_files=[
            ChangedFile(path="api.py", status="modified", additions=1, deletions=1),
            ChangedFile(path="new.py", status="added", additions=1, deletions=0),
        ],
        unified_diff="",
        pr_metadata=(
            PRMetadata(title="t", body="b", number=1, repo="o/r", head_repo="o/r")
            if pr
            else None
        ),
        checkout=checkout,
    )


def _plan(
    scratch: dict[str, Any],
    *,
    prior: ReviewState | None,
    head: str,
    **kw: Any,
) -> DeltaPlan:
    """Plan a round on the scratch repository.

    Args:
        scratch: The fixture.
        prior: Prior state.
        head: The head under review.
        **kw: Overrides for :func:`_ctx` and ``force_full``.

    Returns:
        The plan.
    """
    force_full = kw.pop("force_full", False)
    return plan_delta(
        prior=prior,
        context=_ctx(head=head, base=kw.pop("base", scratch["a"]), **kw),
        repo_root=str(scratch["repo"]),
        force_full=force_full,
    )


def test_round_one_and_missing_heads_read_the_whole_diff(
    scratch: dict[str, Any],
) -> None:
    """No prior round, or a prior round without a head, is a full read."""
    c = scratch["c"]
    assert_that(_plan(scratch, prior=None, head=c).reason).is_equal_to(
        DeltaReason.FIRST_ROUND,
    )
    assert_that(_plan(scratch, prior=ReviewState(), head=c).reason).is_equal_to(
        DeltaReason.FIRST_ROUND,
    )
    assert_that(_plan(scratch, prior=_state(sha=""), head=c).reason).is_equal_to(
        DeltaReason.NO_PRIOR_HEAD,
    )


def test_an_ancestor_prior_head_anchors_a_delta(scratch: dict[str, Any]) -> None:
    """B is an ancestor of C and main did not move: round two reads ``B..C``."""
    plan = _plan(scratch, prior=_state(sha=scratch["b"]), head=scratch["c"])
    assert_that(plan.is_delta).is_true()
    assert_that(plan.since_sha).is_equal_to(scratch["b"])


def test_a_force_push_falls_back_to_a_full_read(scratch: dict[str, Any]) -> None:
    """B is not an ancestor of B': the rewritten branch is read whole."""
    plan = _plan(scratch, prior=_state(sha=scratch["b"]), head=scratch["b_prime"])
    assert_that(plan.is_delta).is_false()
    assert_that(plan.reason).is_equal_to(DeltaReason.NOT_ANCESTOR)


def test_a_prior_head_the_repository_no_longer_holds_is_not_an_ancestor(
    scratch: dict[str, Any],
) -> None:
    """A commit git cannot find (garbage-collected old head) is a full read."""
    plan = _plan(scratch, prior=_state(sha="0" * 40), head=scratch["c"])
    assert_that(plan.reason).is_equal_to(DeltaReason.NOT_ANCESTOR)


def test_full_no_tree_same_head_and_non_pr_never_compute_a_delta(
    scratch: dict[str, Any],
) -> None:
    """Each fallback carries its own reason for the sticky."""
    prior = _state(sha=scratch["b"])
    c = scratch["c"]
    assert_that(
        _plan(scratch, prior=prior, head=c, force_full=True).reason,
    ).is_equal_to(
        DeltaReason.EXPLICIT_FULL,
    )
    assert_that(
        _plan(scratch, prior=prior, head=c, checkout=ReviewCheckout.NONE).reason,
    ).is_equal_to(DeltaReason.NO_TREE)
    assert_that(_plan(scratch, prior=prior, head=scratch["b"]).reason).is_equal_to(
        DeltaReason.SAME_HEAD,
    )
    assert_that(_plan(scratch, prior=prior, head=c, pr=False).reason).is_equal_to(
        DeltaReason.NOT_PR,
    )


def test_a_merge_from_main_since_the_prior_round_is_a_full_read(
    scratch: dict[str, Any],
) -> None:
    """The base entered ``B..head``: its lines must not pass as the PR's delta."""
    repo = scratch["repo"]
    _git(repo, "checkout", "-q", "pr")
    _git(repo, "merge", "-q", "--no-edit", "main")
    merged_head = _git(repo, "rev-parse", "HEAD")
    # Prior record with the merge-base it saw at B (main was at A then).
    prior = ReviewState(
        runs=(
            RunRecord(
                identity=RunIdentity(
                    round=1,
                    sha=scratch["b"],
                    merge_base=scratch["a"],
                ),
            ),
        ),
    )
    plan = _plan(scratch, prior=prior, head=merged_head, base=scratch["m"])
    assert_that(plan.reason).is_equal_to(DeltaReason.BASE_MOVED)
    # A prior record without a merge-base (written before the field): the
    # merge commit in the range is the signal.
    legacy = _state(sha=scratch["b"])
    plan = _plan(scratch, prior=legacy, head=merged_head, base=scratch["m"])
    assert_that(plan.reason).is_equal_to(DeltaReason.BASE_MOVED)
    # And main moving WITHOUT being merged in is still a delta.
    plan = _plan(scratch, prior=prior, head=scratch["c"], base=scratch["m"])
    assert_that(plan.is_delta).is_true()


# --- what a delta round reads -------------------------------------------------


def test_delta_hunks_are_the_change_since_the_prior_head_for_pr_files_only(
    scratch: dict[str, Any],
) -> None:
    """``B..C`` carries C's edit to api.py and the new file, nothing of B's."""
    hunks = delta_hunks(
        repo_root=str(scratch["repo"]),
        since_sha=scratch["b"],
        head_sha=scratch["c"],
        pr_paths=["api.py", "new.py"],
    )
    assert hunks is not None
    assert_that(sorted(hunks)).is_equal_to(["api.py", "new.py"])
    assert_that(hunks["api.py"]).contains("+    return retries")
    # B's change is context now, not a changed line.
    assert_that(hunks["api.py"]).does_not_contain("+def send(payload, *, retries):")
    assert_that(hunks["api.py"]).does_not_contain("-def send(payload):")


def test_a_file_main_changed_under_the_pr_is_never_a_delta_hunk(
    scratch: dict[str, Any],
) -> None:
    """A merge from main brings other.py into ``B..head``; it is not the PR's."""
    repo = scratch["repo"]
    _git(repo, "checkout", "-q", "pr")
    _git(repo, "merge", "-q", "--no-edit", "main")
    head = _git(repo, "rev-parse", "HEAD")
    raw = _git(repo, "diff", f"{scratch['b']}..{head}")
    assert_that(raw).contains("other.py")  # the range does see main's change
    hunks = delta_hunks(
        repo_root=str(repo),
        since_sha=scratch["b"],
        head_sha=head,
        pr_paths=["api.py", "new.py"],
    )
    assert hunks is not None
    assert_that(sorted(hunks)).is_equal_to(["api.py", "new.py"])


def test_a_range_git_cannot_compute_is_reported_as_none(
    scratch: dict[str, Any],
) -> None:
    """A failed diff is not an empty delta: the caller records a full read."""
    hunks = delta_hunks(
        repo_root=str(scratch["repo"]),
        since_sha="0" * 40,
        head_sha=scratch["c"],
        pr_paths=["api.py"],
    )
    assert_that(hunks).is_none()


def test_apply_narrows_only_files_with_a_smaller_delta_hunk(
    scratch: dict[str, Any],
) -> None:
    """The whole-PR hunk stays in ``diff``; ``read_diff`` carries the delta."""
    whole = _git(scratch["repo"], "diff", f"{scratch['a']}..{scratch['c']}")
    per_file = split_unified_diff_by_file(unified_diff=whole)
    chunk = ReviewChunk(
        id=1,
        files=["api.py", "new.py"],
        diff=per_file["api.py"] + per_file["new.py"],
        relationship=REL_SINGLE_FILE,
    )
    hunks = delta_hunks(
        repo_root=str(scratch["repo"]),
        since_sha=scratch["b"],
        head_sha=scratch["c"],
        pr_paths=["api.py"],
    )
    assert hunks is not None
    applied = apply_delta_hunks(chunks=[chunk], hunks=hunks)
    (rebuilt,) = applied.chunks
    assert_that(rebuilt.diff).is_equal_to(chunk.diff)  # the gate's view: untouched
    assert rebuilt.read_diff is not None
    parts = split_unified_diff_by_file(unified_diff=rebuilt.read_diff)
    assert_that(list(parts)).is_equal_to(["api.py", "new.py"])
    assert_that(parts["api.py"]).is_equal_to(hunks["api.py"])
    assert_that(parts["new.py"]).is_equal_to(per_file["new.py"])
    # The lines the round read, for the matcher: api.py's hunk only.
    assert_that({path for path, _, _ in applied.reviewed_ranges}).is_equal_to(
        {"api.py"},
    )
    assert_that(applied.larger).is_empty()
    # Nothing to apply: chunks pass through unchanged.
    assert_that(apply_delta_hunks(chunks=[chunk], hunks={}).chunks).is_equal_to([chunk])
    # A delta that is not smaller than the whole hunk is not used.
    bigger = apply_delta_hunks(chunks=[chunk], hunks={"api.py": hunks["api.py"] * 40})
    assert_that(bigger.chunks[0].read_diff).is_none()
    assert_that(bigger.larger).is_equal_to(("api.py",))


# --- the queue: open threads, and the INCOMPLETE rule -------------------------


def _open(path: str) -> FindingRecord:
    """An open P2 on ``path``.

    Args:
        path: The file.

    Returns:
        The record.
    """
    return FindingRecord(
        fingerprint=f"fp-{path}",
        severity=Severity.P2,
        category="logic-bug",
        title="t",
        file=path,
        line=1,
        status=FindingStatus.OPEN,
        since_round=1,
    )


def test_open_thread_files_are_re_read_even_when_covered() -> None:
    """A covered file with an open finding is queued, behind changed files."""
    prior = ReviewState(
        findings=(
            _open("threaded.py"),
            replace(_open("fixed.py"), status=FindingStatus.RESOLVED),
        ),
    )
    assert_that(open_thread_paths(prior=prior)).is_equal_to(("threaded.py",))
    classified = classify_files(
        request=ClassifyFilesRequest(
            eligible_paths=["changed.py", "threaded.py", "quiet.py"],
            current_hashes={
                "changed.py": "new",
                "threaded.py": "same",
                "quiet.py": "same",
            },
            coverage=(
                CoverageRecord("changed.py", "old"),
                CoverageRecord("threaded.py", "same"),
                CoverageRecord("quiet.py", "same"),
            ),
            open_thread_paths=open_thread_paths(prior=prior),
        ),
    )
    needs = {item.path: item.need for item in classified}
    assert_that(needs["threaded.py"]).is_equal_to(FileReviewNeed.OPEN_THREAD)
    assert_that(needs["quiet.py"]).is_equal_to(FileReviewNeed.COVERED)
    assert_that(queue_paths(classified=classified)).is_equal_to(
        ("changed.py", "threaded.py"),
    )


def test_a_changed_file_a_delta_round_did_not_read_forces_incomplete() -> None:
    """ADR-0007 holds on a delta round: coverage below 100% at HEAD is INCOMPLETE."""
    from lintro.ai.review.coverage import coverage_counts

    classified = classify_files(
        request=ClassifyFilesRequest(
            eligible_paths=["api.py", "other.py"],
            current_hashes={"api.py": "h2", "other.py": "h2"},
            coverage=(CoverageRecord("api.py", "h1"), CoverageRecord("other.py", "h1")),
        ),
    )
    # Both hashes moved; the round only read api.py.
    counts = coverage_counts(classified=classified, reviewed_now=["api.py"])
    assert_that(counts.complete).is_false()
    verdict = apply_coverage_gate(
        findings_verdict=derive_readiness_verdict(findings=()),
        coverage_complete=counts.complete,
    )
    assert_that(verdict).is_equal_to(ReviewVerdict.INCOMPLETE)


# --- the sticky line and the id on the wire -----------------------------------


def _run(*, round_number: int, sha: str, since: str, reason: DeltaReason) -> RunRecord:
    """A recorded round.

    Args:
        round_number: The round.
        sha: Its head.
        since: The delta anchor, or empty.
        reason: The scope reason.

    Returns:
        The record.
    """
    return RunRecord(
        identity=RunIdentity(round=round_number, sha=sha),
        coverage=RunCoverage(delta_since=since, delta_reason=str(reason)),
    )


def test_the_sticky_says_what_the_round_read() -> None:
    """Round one is silent; a delta names its anchor; a full read says why."""
    first = (
        _run(round_number=1, sha="a" * 40, since="", reason=DeltaReason.FIRST_ROUND),
    )
    assert_that(_scope_line(runs=first, round_number=1)).is_empty()
    delta = (
        *first,
        _run(round_number=2, sha="c" * 40, since="b" * 40, reason=DeltaReason.DELTA),
    )
    assert_that(_scope_line(runs=delta, round_number=2)).contains(
        "Round 2 read the delta since `bbbbbbb`",
    )
    forced = (
        *first,
        _run(round_number=2, sha="c" * 40, since="", reason=DeltaReason.NOT_ANCESTOR),
    )
    assert_that(_scope_line(runs=forced, round_number=2)).contains(
        "read the whole diff: the branch was rewritten",
    )
    legacy = (*first, RunRecord(identity=RunIdentity(round=2, sha="c" * 40)))
    assert_that(_scope_line(runs=legacy, round_number=2)).is_empty()
    body = render_state_sticky(state=ReviewState(runs=delta), repo="o/r", pr_number=1)
    assert_that(body).contains("read the delta since `bbbbbbb`")


def test_delta_fields_round_trip_through_the_state_blob() -> None:
    """The two coverage fields survive to_dict/from_dict and stay optional."""
    record = _run(
        round_number=2,
        sha="c" * 40,
        since="b" * 40,
        reason=DeltaReason.DELTA,
    )
    payload = record.to_dict()
    assert_that(payload["delta_since"]).is_equal_to("b" * 40)
    assert_that(RunRecord.from_dict(payload).coverage.delta_reason).is_equal_to("delta")
    plain = RunRecord(identity=RunIdentity(round=1, sha="a" * 40)).to_dict()
    assert_that(plain).does_not_contain_key("delta_since")
    assert_that(plain).does_not_contain_key("delta_reason")


def test_finding_ids_are_stable_across_rounds_and_match_the_record_key(
    sample_review_result: ReviewResult,
) -> None:
    """JSON carries ``finding_id`` = ``<fingerprint>#<ordinal>``, line-independent."""
    first = review_result_to_dict(result=sample_review_result)
    ids = [item["finding_id"] for item in first["findings"]]
    assert_that(ids).is_length(len(sample_review_result.findings))
    assert_that(len(set(ids))).is_equal_to(len(ids))
    # Same findings on a later head with every line shifted: same ids.
    shifted = replace(
        sample_review_result,
        findings=tuple(
            replace(finding, line=finding.line + 40)
            for finding in sample_review_result.findings
        ),
    )
    second = review_result_to_dict(result=shifted)
    assert_that([item["finding_id"] for item in second["findings"]]).is_equal_to(ids)
    # And the id is what the sticky rows and inline threads carry.
    for item in first["findings"]:
        fingerprint, _, ordinal = item["finding_id"].partition("#")
        assert_that(fingerprint).is_length(16)
        assert_that(int(ordinal)).is_greater_than_or_equal_to(1)
    json.dumps(first)  # serializable


# --- resolution is line-scoped on a delta round; ids come from the match ------


def _finding(*, file: str, line: int, title: str = "Leak") -> ReviewFinding:
    """A P2 finding.

    Args:
        file: The file.
        line: The line.
        title: The title (part of the fingerprint).

    Returns:
        The finding.
    """
    return ReviewFinding(
        severity=Severity.P2,
        category="logic-bug",
        file=file,
        line=line,
        title=title,
        description="d",
        cause="c",
        fix="f",
        confidence="high",
    )


def test_a_prior_finding_outside_the_read_range_is_carried_not_resolved() -> None:
    """A delta round narrows what it may resolve: unread lines stay open."""
    prior = ReviewState(
        findings=(
            replace(_open("api.py"), line=5),  # in the read range
            replace(_open("api.py"), fingerprint="fp-2", line=80, title="Other"),  # not
            replace(_open("quiet.py"), line=3),  # file read whole
        ),
        runs=(RunRecord(identity=RunIdentity(round=1, sha="b" * 40)),),
    )
    match = match_findings(
        previous=prior,
        findings=[],  # the round re-reported nothing
        round_number=2,
        head_sha="c" * 40,
        reviewed_paths=frozenset({"api.py", "quiet.py"}),
        reviewed_ranges={"api.py": ((1, 10),)},
    )
    outcomes = dict(match.outcomes.items())
    assert_that(outcomes["fp-api.py#1"]).is_equal_to(FindingMatchOutcome.RESOLVED)
    assert_that(outcomes["fp-2#1"]).is_equal_to(FindingMatchOutcome.CARRIED)
    assert_that(match.range_carries).is_equal_to(frozenset({"fp-2#1"}))
    assert_that(outcomes["fp-quiet.py#1"]).is_equal_to(FindingMatchOutcome.RESOLVED)
    # A full round (no ranges) resolves as before.
    full = match_findings(
        previous=prior,
        findings=[],
        round_number=2,
        head_sha="c" * 40,
        reviewed_paths=frozenset({"api.py", "quiet.py"}),
    )
    assert_that(full.range_carries).is_empty()
    assert_that(full.outcomes["fp-2#1"]).is_equal_to(FindingMatchOutcome.RESOLVED)


def test_reviewed_ranges_ride_on_the_result_for_every_match_site(
    sample_review_result: ReviewResult,
) -> None:
    """The three match sites derive the same ranges from the metadata."""
    assert_that(matcher_reviewed_ranges(result=sample_review_result)).is_none()
    narrowed = replace(
        sample_review_result,
        metadata=replace(
            sample_review_result.metadata,
            reviewed_ranges=(("a.py", 1, 5), ("a.py", 20, 30), ("b.py", 7, 7)),
        ),
    )
    assert_that(matcher_reviewed_ranges(result=narrowed)).is_equal_to(
        {"a.py": ((1, 5), (20, 30)), "b.py": ((7, 7),)},
    )


def test_finding_ids_come_from_the_match_when_siblings_swap_lines(
    sample_review_result: ReviewResult,
) -> None:
    """Two same-fingerprint findings that swapped lines keep their prior keys."""
    fp = fingerprint_for(file="api.py", category="logic-bug", title="Leak")
    prior = ReviewState(
        findings=(
            # Ordinals out of line order: #2 was found later, lower in the file.
            replace(_open("api.py"), fingerprint=fp, ordinal=1, line=100),
            replace(_open("api.py"), fingerprint=fp, ordinal=2, line=10),
        ),
        runs=(RunRecord(identity=RunIdentity(round=1, sha="b" * 40)),),
    )
    # Current sightings, reported high line first.
    result = replace(
        sample_review_result,
        findings=(_finding(file="api.py", line=95), _finding(file="api.py", line=12)),
    )
    stamped = stamp_finding_ids(result=result, prior_state=prior, head_sha="c" * 40)
    ids = [finding.finding_id for finding in stamped.findings]
    # Nearest-line pairing: 95 → #1 (was 100), 12 → #2 (was 10).
    assert_that(ids).is_equal_to([f"{fp}#1", f"{fp}#2"])
    # The JSON surface uses the stamped id, not the line-order recomputation.
    payload = review_result_to_dict(result=stamped)
    assert_that([item["finding_id"] for item in payload["findings"]]).is_equal_to(ids)
    # Recomputed from line order the ids would be the other way round.
    naive = review_result_to_dict(result=result)
    assert_that([item["finding_id"] for item in naive["findings"]]).is_equal_to(
        [f"{fp}#2", f"{fp}#1"],
    )
