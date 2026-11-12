"""Open findings are re-judged on a delta round, not carried unread (#3403).

The fixtures mirror two real pull requests. On #3374 a P3 test gap at
``tests/unit/ai/review/test_lint_bridge.py:81`` was fixed by appending the
requested tests further down the file. On #3391 a P2 at
``lintro/utils/execution/fix_retry.py:96`` was fixed by a change to the merge
block below the loop. Neither finding was posted inline. In both cases the
delta round showed only the new lines, the finding's own line fell outside
every delta hunk, and the matcher carried the finding forward as "—"
(unchanged) round after round. On #3391 that held the verdict at "Changes
requested".

A finding a round still carries without reading it keeps its severity and
its weight in the verdict, and is labelled "not re-verified" on every board,
including one rebuilt from stored state. Its line stays in the coordinates of
the head that last reported it, and is mapped forward before the next delta
check, so an unread round that moved it cannot hide it (the second reviewer's
three-round scenario on #3409).

Every test drives a scratch git repository: no network, no provider.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404 - scratch repositories are driven with fixed git argv
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.ai.review.context.diff_parse import split_unified_diff_by_file
from lintro.ai.review.delta import (
    DeltaApplication,
    apply_delta_hunks,
    delta_hunks,
    plan_delta,
)
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.review_checkout import ReviewCheckout
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.finding_identity import fingerprint_for
from lintro.ai.review.finding_matcher import review_finding_from_record
from lintro.ai.review.github_errors import format_error_comment
from lintro.ai.review.group_labels import REL_SINGLE_FILE
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.coverage_counts import CoverageCounts
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.pr_metadata import PRMetadata
from lintro.ai.review.models.review_chunk import ReviewChunk
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_finding import Severity
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_identity import RunIdentity
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.review_state_codec import decode_state, leftover_state_block
from lintro.ai.review.sticky.assembly import (
    advance_review_state,
    build_sticky_comment,
    render_state_sticky,
)

_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull,
}

#: The #3391 finding: open since round 4, never posted inline.
_FIX_RETRY = "lintro/utils/execution/fix_retry.py"
_FIX_RETRY_TITLE = "Execution-failed retry pass still gets fabricated fix counts"

#: The #3374 finding: a P3 test gap, never posted inline.
_BRIDGE_TEST = "tests/unit/ai/review/test_lint_bridge.py"
_BRIDGE_TITLE = "Bridge black-skip branch has no test coverage"


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


def _lines(count: int, *, label: str) -> str:
    """Return ``count`` numbered filler lines.

    Args:
        count: How many lines.
        label: Prefix that keeps each file's text distinct.

    Returns:
        The text, newline-terminated.
    """
    return "".join(f"{label}_{index} = {index}\n" for index in range(1, count + 1))


@dataclass(frozen=True)
class _Pr:
    """A scratch pull request with one fix pushed after the prior round.

    Attributes:
        repo: The repository.
        base: The base commit.
        prior: The head the prior round reviewed.
        head: The head after the fix.
    """

    repo: Path
    base: str
    prior: str
    head: str


@pytest.fixture
def pr(tmp_path: Path) -> _Pr:
    """Build the two PR shapes: each fix lands far below its finding's line.

    Args:
        tmp_path: Pytest temp dir.

    Returns:
        The repository and its commits.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    fix_retry = repo / _FIX_RETRY
    bridge = repo / _BRIDGE_TEST
    fix_retry.parent.mkdir(parents=True)
    bridge.parent.mkdir(parents=True)
    fix_retry.write_text(_lines(200, label="retry"))
    bridge.write_text(_lines(200, label="bridge"))
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    # The PR's round-4 head: the whole file is reworked.
    _git(repo, "checkout", "-q", "-b", "pr")
    fix_retry.write_text(_lines(200, label="loop"))
    bridge.write_text(_lines(200, label="case"))
    _git(repo, "commit", "-q", "-am", "round 4")
    prior = _git(repo, "rev-parse", "HEAD")
    # The fixes: #3391 widens the merge block at line 180, #3374 appends tests.
    text = fix_retry.read_text().splitlines(keepends=True)
    text[179] = "merge_block = is_incomplete(result)\n"
    fix_retry.write_text("".join(text))
    bridge.write_text(bridge.read_text() + "def test_black_skipped(): ...\n")
    _git(repo, "commit", "-q", "-am", "fix")
    head = _git(repo, "rev-parse", "HEAD")
    return _Pr(repo=repo, base=base, prior=prior, head=head)


def _record(
    *,
    file: str,
    line: int,
    title: str,
    severity: Severity,
    line_sha: str,
) -> FindingRecord:
    """An open record with no inline thread, as on #3374 and #3391.

    Args:
        file: The file.
        line: The finding's line.
        title: The finding's title.
        severity: The finding's severity.
        line_sha: The head whose coordinates ``line`` is in.

    Returns:
        The record.
    """
    category = "test-gap" if severity is Severity.P3 else "logic-bug"
    return FindingRecord(
        fingerprint=fingerprint_for(file=file, category=category, title=title),
        severity=severity,
        category=category,
        title=title,
        file=file,
        line=line,
        status=FindingStatus.OPEN,
        since_round=4,
        inline_comment_id=None,
        description="d",
        cause="c",
        fix="f",
        confidence="high",
        line_sha=line_sha,
    )


def _prior_state(*, prior_head: str, severity: Severity = Severity.P2) -> ReviewState:
    """State after round 4: two open, unthreaded findings seen at its head.

    Args:
        prior_head: The head round 4 reviewed.
        severity: Severity of the ``fix_retry.py`` finding.

    Returns:
        The state.
    """
    return ReviewState(
        findings=(
            _record(
                file=_FIX_RETRY,
                line=96,
                title=_FIX_RETRY_TITLE,
                severity=severity,
                line_sha=prior_head,
            ),
            _record(
                file=_BRIDGE_TEST,
                line=81,
                title=_BRIDGE_TITLE,
                severity=Severity.P3,
                line_sha=prior_head,
            ),
        ),
        runs=(RunRecord(identity=RunIdentity(round=4, sha=prior_head)),),
        head_sha=prior_head,
    )


def _context(*, repo: Path, base: str, head: str, paths: list[str]) -> ReviewContext:
    """A ``--pr`` context over the scratch repository.

    Args:
        repo: The repository.
        base: The base commit.
        head: The head under review.
        paths: The files the pull request changes.

    Returns:
        The context.
    """
    return ReviewContext(
        base_ref=base,
        head_ref=head,
        changed_files=[
            ChangedFile(path=path, status="modified", additions=1, deletions=1)
            for path in paths
        ],
        unified_diff="",
        repo_root=str(repo),
        checkout=ReviewCheckout.HEAD,
        pr_metadata=PRMetadata(title="t", body="b", number=1, repo="o/r"),
    )


def _plan_round(
    *,
    repo: Path,
    base: str,
    head: str,
    prior: ReviewState,
) -> DeltaApplication:
    """Plan a delta round and apply it to one chunk per changed file.

    Args:
        repo: The repository.
        base: The base commit.
        head: The head under review.
        prior: State carried into the round.

    Returns:
        The delta application the run planner would use.
    """
    whole = _git(repo, "diff", f"{base}..{head}")
    per_file = split_unified_diff_by_file(unified_diff=whole)
    plan = plan_delta(
        prior=prior,
        context=_context(repo=repo, base=base, head=head, paths=list(per_file)),
        repo_root=str(repo),
        force_full=False,
    )
    assert plan.is_delta and plan.since_sha is not None
    hunks = delta_hunks(
        repo_root=str(repo),
        since_sha=plan.since_sha,
        head_sha=head,
        pr_paths=list(per_file),
    )
    assert hunks is not None
    chunks = [
        ReviewChunk(id=index, files=[path], diff=diff, relationship=REL_SINGLE_FILE)
        for index, (path, diff) in enumerate(sorted(per_file.items()), start=1)
    ]
    return apply_delta_hunks(
        chunks=chunks,
        hunks=hunks,
        since_sha=plan.since_sha,
        open_lines=plan.open_lines,
    )


def _round_result(
    *,
    reviewed_paths: tuple[str, ...],
    reviewed_ranges: tuple[tuple[str, int, int], ...] = (),
    eligible: int | None = None,
) -> ReviewResult:
    """A round's result in which the reviewer re-reported nothing.

    Args:
        reviewed_paths: Files the round read.
        reviewed_ranges: Delta ranges the round recorded.
        eligible: Review-eligible files; when set, the round carries coverage
            counts in which every eligible file was reviewed (complete).

    Returns:
        The result.
    """
    return ReviewResult(
        metadata=ReviewMetadata(
            model="m",
            provider="anthropic",
            context_window=200_000,
            depth=1,
            chunks_total=1,
            chunks_current=1,
            files_reviewed=len(reviewed_paths),
            files_total=len(reviewed_paths),
            checklist_items=0,
            reviewed_paths=reviewed_paths,
            reviewed_ranges=reviewed_ranges,
        ),
        summary="Nothing blocks the merge.",
        findings=(),
        coverage=(
            None
            if eligible is None
            else CoverageCounts(reviewed=eligible, eligible=eligible)
        ),
    )


def _sticky_request(
    *,
    prior: ReviewState,
    result: ReviewResult,
    head: str,
) -> StickyRequest:
    """Build a round's sticky request.

    Args:
        prior: State carried into the round.
        result: The round's result.
        head: The round's head.

    Returns:
        The request.
    """
    return StickyRequest(
        result=result,
        prior_state=prior,
        head_sha=head,
        repo="lgtm-hq/py-lintro",
        pr_number=3391,
    )


def _row(*, body: str, title: str) -> str:
    """Return the sticky table row naming ``title``.

    Args:
        body: The rendered sticky.
        title: The finding's title.

    Returns:
        The row.
    """
    return next(line for line in body.splitlines() if title in line)


def _unread_round(*, pr: _Pr, severity: Severity) -> StickyRequest:
    """Round 5 when ``fix_retry.py`` was out of the round's scope.

    A file dropped by a path filter, config exclusion or agent scope is not
    review-eligible (``coverage.eligible_review_paths``), so coverage stays
    complete while the open finding on it is carried unread.

    Args:
        pr: The scratch pull request.
        severity: Severity of the unread finding.

    Returns:
        The round's sticky request.
    """
    return _sticky_request(
        prior=_prior_state(prior_head=pr.prior, severity=severity),
        result=_round_result(reviewed_paths=(_BRIDGE_TEST,), eligible=1),
        head=pr.head,
    )


def test_a_fixed_unthreaded_finding_is_reported_fixed_next_round(pr: _Pr) -> None:
    """A fix below the finding's line no longer leaves it carried as unchanged."""
    prior = _prior_state(prior_head=pr.prior)
    applied = _plan_round(repo=pr.repo, base=pr.base, head=pr.head, prior=prior)
    # Both files hold an open finding the delta would not show: read whole.
    assert_that(applied.reread).contains(_FIX_RETRY, _BRIDGE_TEST)
    request = _sticky_request(
        prior=prior,
        result=_round_result(
            reviewed_paths=(_BRIDGE_TEST, _FIX_RETRY),
            reviewed_ranges=applied.reviewed_ranges,
        ),
        head=pr.head,
    )

    body = build_sticky_comment(request=request)
    state = advance_review_state(request=request)

    assert_that(body).contains("## 🔎 Lintro Review — ✅ Ready")
    assert_that(body).contains("0 open · 2 fixed this round")
    assert_that(body).contains(f"~~{_FIX_RETRY_TITLE}~~")
    statuses = {record.title: record.status for record in state.findings}
    assert_that(statuses).is_equal_to(
        {
            _FIX_RETRY_TITLE: FindingStatus.RESOLVED,
            _BRIDGE_TITLE: FindingStatus.RESOLVED,
        },
    )
    assert_that({record.resolved_sha for record in state.findings}).is_equal_to(
        {pr.head},
    )
    assert_that(state.runs[-1].outcome.verdict).is_equal_to(ReviewVerdict.READY)


def test_a_finding_inside_the_delta_still_narrows_its_file(pr: _Pr) -> None:
    """Narrowing is kept when the delta already shows every open line."""
    prior = _prior_state(prior_head=pr.prior)
    shown = replace(prior.findings[0], line=180)
    applied = _plan_round(
        repo=pr.repo,
        base=pr.base,
        head=pr.head,
        prior=replace(prior, findings=(shown,)),
    )
    narrowed = {path for path, _, _ in applied.reviewed_ranges}
    assert_that(narrowed).contains(_FIX_RETRY, _BRIDGE_TEST)
    assert_that(applied.reread).is_empty()


@pytest.mark.parametrize(
    ("severity", "heading", "reasoning", "verdict"),
    [
        (
            Severity.P1,
            "## 🔎 Lintro Review — ⛔ Blocked",
            "### Why it's blocked",
            ReviewVerdict.BLOCKED,
        ),
        (
            Severity.P2,
            "## 🔎 Lintro Review — ⚠️ Changes requested",
            "### Why changes are requested",
            ReviewVerdict.CHANGES_REQUESTED,
        ),
    ],
)
def test_an_unread_finding_keeps_its_severity_and_is_labelled(
    pr: _Pr,
    severity: Severity,
    heading: str,
    reasoning: str,
    verdict: ReviewVerdict,
) -> None:
    """A finding nothing re-read keeps its verdict weight and gets the label."""
    request = _unread_round(pr=pr, severity=severity)

    body = build_sticky_comment(request=request)
    state = advance_review_state(request=request)

    # The header, the reasoning heading and the row agree on the severity.
    assert_that(body).contains(heading, reasoning)
    assert_that(body).does_not_contain("Nits only", "Why it's flagged")
    assert_that(_row(body=body, title=_FIX_RETRY_TITLE)).starts_with(
        f"| ⏸ not re-verified | {_severity_cell(severity)} |",
    )
    assert_that(state.runs[-1].outcome.verdict).is_equal_to(verdict)
    record = next(item for item in state.findings if item.title == _FIX_RETRY_TITLE)
    assert_that(record.status).is_equal_to(FindingStatus.OPEN)
    assert_that(record.severity).is_equal_to(severity)
    assert_that(record.unverified).is_true()
    # Carried unread, so its line is still in round 4's coordinates.
    assert_that((record.line, record.line_sha)).is_equal_to((96, pr.prior))


def _severity_cell(severity: Severity) -> str:
    """Render the severity cell the sticky uses.

    Args:
        severity: The severity.

    Returns:
        The cell text.
    """
    return {Severity.P1: "🔴 P1", Severity.P2: "🟠 P2", Severity.P3: "🟡 P3"}[severity]


def test_the_label_survives_persistence_and_clears_once_re_read(pr: _Pr) -> None:
    """Boards rebuilt from stored state keep the label; a re-read drops it."""
    stored = advance_review_state(request=_unread_round(pr=pr, severity=Severity.P2))
    artifact = ReviewState.from_artifact_dict(
        json.loads(json.dumps(stored.to_artifact_dict())),
    )
    blob = decode_state(body=leftover_state_block(state=stored))
    label = "| ⏸ not re-verified | 🟠 P2 |"
    for restored in (artifact, blob):
        assert_that(
            _row(body=render_state_sticky(state=restored), title=_FIX_RETRY_TITLE),
        ).starts_with(label)
        error_board = format_error_comment(
            error=RuntimeError("provider unavailable"),
            prior_state=restored,
        )
        assert_that(_row(body=error_board, title=_FIX_RETRY_TITLE)).starts_with(label)

    record = next(item for item in artifact.findings if item.title == _FIX_RETRY_TITLE)
    re_read = _sticky_request(
        prior=artifact,
        result=replace(
            _round_result(reviewed_paths=(_FIX_RETRY,), eligible=1),
            findings=(review_finding_from_record(record=record),),
        ),
        head="c" * 40,
    )
    body = build_sticky_comment(request=re_read)
    after = advance_review_state(request=re_read)
    assert_that(_row(body=body, title=_FIX_RETRY_TITLE)).starts_with("| — | 🟠 P2 |")
    again = next(item for item in after.findings if item.title == _FIX_RETRY_TITLE)
    assert_that((again.unverified, again.line_sha)).is_equal_to((False, "c" * 40))


def test_state_written_before_the_fields_still_loads() -> None:
    """A record without ``line_sha``/``unverified`` loads, and round-trips as is."""
    payload = {
        "fingerprint": "abc",
        "ordinal": 1,
        "severity": "P2",
        "category": "logic-bug",
        "title": "t",
        "file": "a.py",
        "line": 3,
        "status": "open",
        "since_round": 1,
    }
    record = FindingRecord.from_dict(payload)
    assert record is not None
    assert_that((record.line_sha, record.unverified)).is_equal_to(("", False))
    assert_that(record.to_dict()).is_equal_to(payload)


# --- Sol's three rounds: an unread round moves the finding's real line -------

_LARGE = "large.py"


@pytest.fixture
def drift(tmp_path: Path) -> dict[str, str | Path]:
    """X is reported at line 81; ten lines land above it in an unread round.

    Args:
        tmp_path: Pytest temp dir.

    Returns:
        The repository and the ``base``, ``h1`` (X reported), ``h2``
        (insertion above X, a round that did not read the file) and ``h3``
        (an edit at the stale line 81, which is not X) and ``h4`` (an edit
        at line 250) commits.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    path = repo / _LARGE
    path.write_text(_lines(300, label="base"))
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "pr")
    path.write_text(_lines(300, label="case"))
    _git(repo, "commit", "-q", "-am", "round 1")
    h1 = _git(repo, "rev-parse", "HEAD")
    path.write_text(_lines(10, label="inserted") + path.read_text())
    _git(repo, "commit", "-q", "-am", "round 2: insert above X")
    h2 = _git(repo, "rev-parse", "HEAD")
    text = path.read_text().splitlines(keepends=True)
    text[80] = "unrelated_to_x = True\n"
    path.write_text("".join(text))
    _git(repo, "commit", "-q", "-am", "round 3: edit the stale line")
    h3 = _git(repo, "rev-parse", "HEAD")
    text[249] = "far_edit = True\n"
    path.write_text("".join(text))
    _git(repo, "commit", "-q", "-am", "round 4: edit far below")
    h4 = _git(repo, "rev-parse", "HEAD")
    return {"repo": repo, "base": base, "h1": h1, "h2": h2, "h3": h3, "h4": h4}


def _x_state(*, h1: str, line_sha: str) -> ReviewState:
    """State after round 1: X open at line 81.

    Args:
        h1: Round 1's head.
        line_sha: The head recorded for X's line.

    Returns:
        The state.
    """
    return ReviewState(
        findings=(
            _record(
                file=_LARGE,
                line=81,
                title="X",
                severity=Severity.P2,
                line_sha=line_sha,
            ),
        ),
        runs=(RunRecord(identity=RunIdentity(round=1, sha=h1)),),
        head_sha=h1,
    )


def test_a_line_carried_through_an_unread_round_is_mapped_forward(
    drift: dict[str, str | Path],
) -> None:
    """X's stale line 81 maps to 91, outside round 3's delta: the file is read whole."""
    repo, base = Path(drift["repo"]), str(drift["base"])
    h1, h2, h3 = str(drift["h1"]), str(drift["h2"]), str(drift["h3"])
    # Round 2 did not read large.py: X is carried, its line still at h1.
    carried = advance_review_state(
        request=_sticky_request(
            prior=_x_state(h1=h1, line_sha=h1),
            result=_round_result(reviewed_paths=("other.py",), eligible=1),
            head=h2,
        ),
    )
    (x,) = carried.findings
    assert_that((x.line, x.line_sha, x.unverified)).is_equal_to((81, h1, True))

    applied = _plan_round(repo=repo, base=base, head=h3, prior=carried)

    # Round 3's delta (h2..h3) shows the stale line 81 but not X at 91.
    whole = _git(repo, "diff", f"{h2}..{h3}")
    assert_that(whole).contains("@@ -78,7 +78,7 @@")
    assert_that(applied.reread).is_equal_to((_LARGE,))
    assert_that(applied.reviewed_ranges).is_empty()
    (chunk,) = applied.chunks
    assert_that(chunk.read_diff).is_none()
    assert_that(chunk.diff).contains("+case_81 = 81")


@pytest.mark.parametrize(
    "line_sha",
    ["", "0" * 40],
    ids=["written-before-line_sha", "unknown-commit"],
)
def test_an_unmappable_line_reads_the_file_whole(
    drift: dict[str, str | Path],
    line_sha: str,
) -> None:
    """Without a usable coordinate head the round fails closed and reads whole."""
    repo, base = Path(drift["repo"]), str(drift["base"])
    h1, h2, h3 = str(drift["h1"]), str(drift["h2"]), str(drift["h3"])
    prior = replace(
        _x_state(h1=h1, line_sha=line_sha),
        runs=(RunRecord(identity=RunIdentity(round=2, sha=h2)),),
    )
    applied = _plan_round(repo=repo, base=base, head=h3, prior=prior)
    assert_that(applied.reread).is_equal_to((_LARGE,))


def test_a_mapped_line_inside_the_delta_keeps_narrowing(
    drift: dict[str, str | Path],
) -> None:
    """A carried line mapped into the delta's hunk needs no whole read."""
    repo, base = Path(drift["repo"]), str(drift["base"])
    h1, h2, h3 = str(drift["h1"]), str(drift["h2"]), str(drift["h3"])
    # Line 71 at h1 is line 81 at h2, the very line h2..h3 edits.
    prior = replace(
        _x_state(h1=h1, line_sha=h1),
        runs=(RunRecord(identity=RunIdentity(round=2, sha=h2)),),
    )
    shown = replace(prior, findings=(replace(prior.findings[0], line=71),))
    applied = _plan_round(repo=repo, base=base, head=h3, prior=shown)
    assert_that(applied.reread).is_empty()
    assert_that({path for path, _, _ in applied.reviewed_ranges}).is_equal_to(
        {_LARGE},
    )


def test_a_line_edited_since_it_was_recorded_reads_the_file_whole(
    drift: dict[str, str | Path],
) -> None:
    """A line the carry range itself rewrote cannot be placed: read whole."""
    repo, base = Path(drift["repo"]), str(drift["base"])
    h2, h3, h4 = str(drift["h2"]), str(drift["h3"]), str(drift["h4"])
    # X recorded at h2's line 81, which h2..h3 rewrote; round 4 reads h3..h4,
    # a far-away edit, so only the failed mapping can widen the read.
    prior = replace(
        _x_state(h1=h2, line_sha=h2),
        runs=(RunRecord(identity=RunIdentity(round=3, sha=h3)),),
    )
    applied = _plan_round(repo=repo, base=base, head=h4, prior=prior)
    assert_that(applied.reread).is_equal_to((_LARGE,))
    # A finding recorded at h3 on the far edit's own line keeps the delta.
    at_far_edit = replace(
        prior,
        findings=(replace(prior.findings[0], line=250, line_sha=h3),),
    )
    narrowed = _plan_round(repo=repo, base=base, head=h4, prior=at_far_edit)
    assert_that(narrowed.reread).is_empty()
