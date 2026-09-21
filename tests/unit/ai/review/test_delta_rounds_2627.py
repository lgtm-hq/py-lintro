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
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.review_checkout import ReviewCheckout
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.group_labels import REL_SINGLE_FILE
from lintro.ai.review.models.coverage_record import CoverageRecord
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_chunk import ReviewChunk
from lintro.ai.review.models.review_finding import Severity
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_coverage import RunCoverage
from lintro.ai.review.models.run_identity import RunIdentity
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.output import review_result_to_dict
from lintro.ai.review.sticky.assembly import render_state_sticky
from lintro.ai.review.sticky.scope import _scope_line
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


def test_round_one_and_missing_heads_read_the_whole_diff(
    scratch: dict[str, Any],
) -> None:
    """No prior round, or a prior round without a head, is a full read."""
    root = str(scratch["repo"])
    kwargs: dict[str, Any] = {
        "head_sha": scratch["c"],
        "repo_root": root,
        "checkout": ReviewCheckout.HEAD,
        "force_full": False,
    }
    assert_that(plan_delta(prior=None, **kwargs).reason).is_equal_to(
        DeltaReason.FIRST_ROUND,
    )
    assert_that(plan_delta(prior=ReviewState(), **kwargs).reason).is_equal_to(
        DeltaReason.FIRST_ROUND,
    )
    assert_that(plan_delta(prior=_state(sha=""), **kwargs).reason).is_equal_to(
        DeltaReason.NO_PRIOR_HEAD,
    )


def test_an_ancestor_prior_head_anchors_a_delta(scratch: dict[str, Any]) -> None:
    """B is an ancestor of C: round two reads ``B..C``."""
    plan = plan_delta(
        prior=_state(sha=scratch["b"]),
        head_sha=scratch["c"],
        repo_root=str(scratch["repo"]),
        checkout=ReviewCheckout.HEAD,
        force_full=False,
    )
    assert_that(plan.is_delta).is_true()
    assert_that(plan.since_sha).is_equal_to(scratch["b"])


def test_a_force_push_falls_back_to_a_full_read(scratch: dict[str, Any]) -> None:
    """B is not an ancestor of B': the rewritten branch is read whole."""
    plan = plan_delta(
        prior=_state(sha=scratch["b"]),
        head_sha=scratch["b_prime"],
        repo_root=str(scratch["repo"]),
        checkout=ReviewCheckout.HEAD,
        force_full=False,
    )
    assert_that(plan.is_delta).is_false()
    assert_that(plan.reason).is_equal_to(DeltaReason.NOT_ANCESTOR)


def test_a_prior_head_the_repository_no_longer_holds_is_not_an_ancestor(
    scratch: dict[str, Any],
) -> None:
    """A commit git cannot find (garbage-collected old head) is a full read."""
    plan = plan_delta(
        prior=_state(sha="0" * 40),
        head_sha=scratch["c"],
        repo_root=str(scratch["repo"]),
        checkout=ReviewCheckout.HEAD,
        force_full=False,
    )
    assert_that(plan.reason).is_equal_to(DeltaReason.NOT_ANCESTOR)


def test_full_and_no_tree_win_over_an_ancestor(scratch: dict[str, Any]) -> None:
    """``--full`` and a treeless run never compute a delta."""
    prior = _state(sha=scratch["b"])
    root = str(scratch["repo"])
    assert_that(
        plan_delta(
            prior=prior,
            head_sha=scratch["c"],
            repo_root=root,
            checkout=ReviewCheckout.HEAD,
            force_full=True,
        ).reason,
    ).is_equal_to(DeltaReason.EXPLICIT_FULL)
    assert_that(
        plan_delta(
            prior=prior,
            head_sha=scratch["c"],
            repo_root=root,
            checkout=ReviewCheckout.NONE,
            force_full=False,
        ).reason,
    ).is_equal_to(DeltaReason.NO_TREE)


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
        # The PR's whole diff (three-dot, against main) names api.py and
        # new.py; other.py is main's change, not the PR's.
        pr_paths=["api.py", "new.py"],
    )
    assert_that(sorted(hunks)).is_equal_to(["api.py", "new.py"])


def test_apply_swaps_only_files_with_a_delta_hunk(scratch: dict[str, Any]) -> None:
    """A queued file without a delta hunk keeps its whole-PR text."""
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
    (rebuilt,) = apply_delta_hunks(chunks=[chunk], hunks=hunks)
    parts = split_unified_diff_by_file(unified_diff=rebuilt.diff)
    assert_that(list(parts)).is_equal_to(["api.py", "new.py"])
    assert_that(parts["api.py"]).is_equal_to(hunks["api.py"])
    assert_that(parts["new.py"]).is_equal_to(per_file["new.py"])
    assert_that(apply_delta_hunks(chunks=[chunk], hunks={})).is_equal_to([chunk])


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
