"""Mapping a carried finding's location forward is exact (#3403, #3409 round 3).

A finding carried through an unread round keeps the line of the head that last
reported it. Before the next delta check that line is mapped forward through
git, and the matcher then judges the finding at the mapped location. These
tests pin the edges a reviewer reproduced: a filename git would read as a
glob, a configured text-conversion driver, a rename, and the matcher using the
mapped line in a provider-backed round.

Every test drives a scratch git repository; the provider call is served
canned findings, so nothing reaches the network.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.providers.response import AIResponse
from lintro.ai.review.delta import delta_hunks, plan_delta
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.review_checkout import ReviewCheckout
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.delta_plan import DeltaPlan
from lintro.ai.review.models.pr_metadata import PRMetadata
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_finding import Severity
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_identity import RunIdentity
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.orchestrator import run_review
from lintro.ai.review.session import ReviewSessionOptions
from lintro.ai.review.sticky.assembly import (
    advance_review_state,
    build_sticky_comment,
)
from lintro.config.review_config import ReviewVerifyMode
from tests.unit.ai.review.test_carried_reverify_3403 import (
    _git,
    _lines,
    _plan_round,
    _record,
    _round_result,
    _sticky_request,
)
from tests.unit.ai.review.test_resume_fake_transport import _provider


def _repo(root: Path, names: list[str]) -> tuple[str, str]:
    """Create a repository whose PR rewrote every named file.

    Args:
        root: Directory to create the repository in.
        names: The files.

    Returns:
        ``(base, first_head)``.
    """
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    for name in names:
        (root / name).write_text(_lines(300, label="base"))
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "base")
    base = _git(root, "rev-parse", "HEAD")
    for name in names:
        (root / name).write_text(_lines(300, label="case"))
    _git(root, "commit", "-q", "-am", "first")
    return base, _git(root, "rev-parse", "HEAD")


def _commit(repo: Path, message: str) -> str:
    """Commit every change and return the new head.

    Args:
        repo: The repository.
        message: The commit message.

    Returns:
        The new head.
    """
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _x_carried(*, path: str, line: int, line_sha: str, since: str) -> ReviewState:
    """State in which a P1 ``X`` was carried unread since ``line_sha``.

    Args:
        path: X's file at ``line_sha``.
        line: X's line at ``line_sha``.
        line_sha: The head that last reported X.
        since: The head of the last finished round.

    Returns:
        The state.
    """
    return ReviewState(
        findings=(
            replace(
                _record(
                    file=path,
                    line=line,
                    title="X",
                    severity=Severity.P1,
                    line_sha=line_sha,
                ),
                unverified=True,
            ),
        ),
        runs=(RunRecord(identity=RunIdentity(round=2, sha=since)),),
        head_sha=since,
    )


def _relocated(*, result: ReviewResult, plan: DeltaPlan) -> ReviewResult:
    """Attach a plan's relocations to a result, as the run assembler does.

    Args:
        result: The round's result.
        plan: The round's delta plan.

    Returns:
        The result carrying ``relocated_findings``.
    """
    return replace(
        result,
        metadata=replace(result.metadata, relocated_findings=plan.relocations),
    )


def _edit_line(path: Path, index: int, text: str) -> None:
    """Replace one line of a file.

    Args:
        path: The file.
        index: Zero-based line index.
        text: The new line, newline included.
    """
    lines = path.read_text().splitlines(keepends=True)
    lines[index] = text
    path.write_text("".join(lines))


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
        unified_diff=_git(repo, "diff", f"{base}..{head}") + "\n",
        repo_root=str(repo),
        checkout=ReviewCheckout.HEAD,
        pr_metadata=PRMetadata(title="t", body="", number=1, repo="o/r"),
    )


def test_a_glob_like_filename_maps_only_its_own_hunks(tmp_path: Path) -> None:
    """``odd[1].py`` is a literal path: ``odd1.py``'s opposite shift is ignored."""
    repo = tmp_path / "repo"
    target, other = "odd[1].py", "odd1.py"
    base, h1 = _repo(repo, [target, other])
    # Ten lines land above X in odd[1].py while odd1.py loses ten lines: as a
    # glob, the two shifts would cancel and X would map to 81 instead of 91.
    shifted = repo / target
    shifted.write_text(_lines(10, label="inserted") + shifted.read_text())
    (repo / other).write_text(
        "".join((repo / other).read_text().splitlines(keepends=True)[10:]),
    )
    h2 = _commit(repo, "opposite shifts")
    _edit_line(repo / target, 80, "unrelated = True\n")
    h3 = _commit(repo, "edit the stale line")
    prior = _x_carried(path=target, line=81, line_sha=h1, since=h2)

    plan = plan_delta(
        prior=prior,
        context=_context(repo=repo, base=base, head=h3, paths=[target, other]),
        repo_root=str(repo),
        force_full=False,
    )
    applied = _plan_round(repo=repo, base=base, head=h3, prior=prior)
    after = advance_review_state(
        request=_sticky_request(
            prior=prior,
            result=_relocated(
                result=_round_result(
                    reviewed_paths=(target, other),
                    reviewed_ranges=applied.reviewed_ranges,
                    eligible=2,
                ),
                plan=plan,
            ),
            head=h3,
        ),
    )

    assert_that(dict(plan.open_lines)).is_equal_to({target: (91,)})
    assert_that(applied.reread).is_equal_to((target,))
    assert_that([path for path, _, _ in applied.reviewed_ranges]).does_not_contain(
        target,
    )
    # Read whole, X was judged at its real line; nothing narrowed it away.
    assert_that(after.findings[0].line).is_equal_to(91)


def test_a_textconv_driver_does_not_shift_coordinates(tmp_path: Path) -> None:
    """Raw lines are compared even when a driver rewrites the diff's text."""
    repo = tmp_path / "repo"
    target = "large.py"
    base, _ = _repo(repo, [target])
    converter = tmp_path / "convert.sh"
    converter.write_text(
        '#!/bin/sh\ni=0\nwhile [ "$i" -lt 20 ]; do echo HEADER; i=$((i+1)); done\n'
        'cat "$1"\n',
    )
    converter.chmod(0o700)
    _git(repo, "config", "diff.prefix.textconv", str(converter))
    (repo / ".gitattributes").write_text("large.py diff=prefix\n")
    h1 = _commit(repo, "attributes")
    lines = (repo / target).read_text().splitlines(keepends=True)
    lines[70:70] = _lines(10, label="inserted").splitlines(keepends=True)
    (repo / target).write_text("".join(lines))
    h2 = _commit(repo, "insert above X at 70")
    _edit_line(repo / target, 60, "unrelated = True\n")
    h3 = _commit(repo, "edit line 61")
    prior = _x_carried(path=target, line=81, line_sha=h1, since=h2)

    plan = plan_delta(
        prior=prior,
        context=_context(repo=repo, base=base, head=h3, paths=[target]),
        repo_root=str(repo),
        force_full=False,
    )
    hunks = delta_hunks(
        repo_root=str(repo),
        since_sha=h2,
        head_sha=h3,
        pr_paths=[target],
    )
    applied = _plan_round(repo=repo, base=base, head=h3, prior=prior)

    assert_that(dict(plan.open_lines)).is_equal_to({target: (91,)})
    assert hunks is not None
    assert_that(hunks[target]).does_not_contain("HEADER")
    assert_that(hunks[target]).contains("@@ -58,7 +58,7 @@")
    assert_that(applied.reread).is_equal_to((target,))


def test_a_rename_in_an_unread_round_follows_the_file(tmp_path: Path) -> None:
    """X on ``old.py`` is placed on ``new.py``, whose delta must show it."""
    repo = tmp_path / "repo"
    base, h1 = _repo(repo, ["old.py"])
    _git(repo, "mv", "old.py", "new.py")
    h2 = _commit(repo, "unread rename")
    _edit_line(repo / "new.py", 249, "unrelated = True\n")
    h3 = _commit(repo, "far edit")
    prior = _x_carried(path="old.py", line=81, line_sha=h1, since=h2)

    plan = plan_delta(
        prior=prior,
        context=_context(repo=repo, base=base, head=h3, paths=["new.py"]),
        repo_root=str(repo),
        force_full=False,
    )
    applied = _plan_round(repo=repo, base=base, head=h3, prior=prior)
    after = advance_review_state(
        request=_sticky_request(
            prior=prior,
            result=_relocated(
                result=_round_result(
                    reviewed_paths=("new.py",),
                    reviewed_ranges=applied.reviewed_ranges,
                    eligible=1,
                ),
                plan=plan,
            ),
            head=h3,
        ),
    )

    assert_that(dict(plan.open_lines)).is_equal_to({"new.py": (81,)})
    key = prior.findings[0].key
    assert_that(plan.relocations).is_equal_to(((key, "new.py", 81, h2),))
    assert_that(applied.reread).is_equal_to(("new.py",))
    assert_that(applied.reviewed_ranges).is_empty()
    # Unread on old.py would have kept it "not re-verified"; on new.py, read
    # whole and not re-reported, it is judged there.
    assert_that(after.findings[0].file).is_equal_to("new.py")
    assert_that(after.findings[0].status).is_equal_to(FindingStatus.RESOLVED)


def test_an_ambiguous_rename_reads_the_destination_whole(tmp_path: Path) -> None:
    """Renamed away and re-added at the old path: no single home, read both."""
    repo = tmp_path / "repo"
    base, h1 = _repo(repo, ["old.py"])
    _git(repo, "mv", "old.py", "new.py")
    (repo / "old.py").write_text(_lines(40, label="fresh"))
    h2 = _commit(repo, "rename and re-add")
    _edit_line(repo / "new.py", 249, "unrelated = True\n")
    h3 = _commit(repo, "far edit")
    prior = _x_carried(path="old.py", line=81, line_sha=h1, since=h2)

    plan = plan_delta(
        prior=prior,
        context=_context(repo=repo, base=base, head=h3, paths=["new.py", "old.py"]),
        repo_root=str(repo),
        force_full=False,
    )

    assert_that(dict(plan.open_lines)).is_equal_to({"new.py": None})
    applied = _plan_round(repo=repo, base=base, head=h3, prior=prior)
    assert_that(applied.reread).contains("new.py")


# --- the matcher judges the mapped line (provider-backed) --------------------

_LARGE = "large.py"


def _finding(*, title: str, line: int) -> dict[str, Any]:
    """A canned P2 finding on ``large.py``.

    Args:
        title: The finding's title.
        line: The finding's line.

    Returns:
        The finding as the provider would return it.
    """
    return {
        "severity": "P2",
        "category": "logic-bug",
        "file": _LARGE,
        "line": line,
        "title": title,
        "description": "Reproducible bug",
        "cause": "bad logic",
        "fix": "repair the logic",
        "confidence": "high",
    }


def _review(
    *,
    repo: Path,
    base: str,
    prior: ReviewState | None,
    reported: list[dict[str, Any]],
) -> tuple[ReviewResult, ReviewState, str]:
    """Run one real review round with a canned provider answer.

    Args:
        repo: The repository at the round's head.
        base: The base commit.
        prior: State carried into the round.
        reported: Findings the provider returns.

    Returns:
        The result, the advanced state and the sticky body.
    """
    head = _git(repo, "rev-parse", "HEAD")

    def canned(**_: Any) -> AIResponse:
        """Serve the round's canned findings, whatever the call asked.

        Returns:
            The canned response.
        """
        return AIResponse(
            content=json.dumps(
                {"summary": "Canned review", "checklist": [], "findings": reported},
            ),
            model="fake-model",
            provider="fake",
            input_tokens=10,
            output_tokens=5,
            cost_estimate=0.01,
        )

    context = _context(repo=repo, base=base, head=head, paths=[_LARGE])
    with patch("lintro.ai.review.provider_call.call_ai", side_effect=canned):
        result = run_review(
            context,
            options=ReviewSessionOptions(
                provider=_provider(),
                ai_config=AIConfig(
                    enabled=True,
                    review=True,
                    review_generated_questions=False,
                    transport=AITransport.API,
                ),
                depth=1,
                checklist_items=[],
                checklist_text="",
                classifications=[],
                prior_state=prior,
                enforce_cost_cap=False,
                verify=ReviewVerifyMode.OFF,
            ),
        )
    result = replace(
        result,
        metadata=replace(result.metadata, model_verification="verified"),
    )
    request = StickyRequest(result=result, prior_state=prior, head_sha=head)
    return (
        result,
        advance_review_state(request=request),
        build_sticky_comment(request=request),
    )


def test_the_matcher_judges_a_fix_at_the_mapped_line(tmp_path: Path) -> None:
    """X moved 81 → 91 in an unread round; the fix at 91 resolves it."""
    repo = tmp_path / "repo"
    base, _ = _repo(repo, [_LARGE])
    first, s1, _ = _review(
        repo=repo,
        base=base,
        prior=None,
        reported=[_finding(title="X", line=81)],
    )
    h1 = s1.head_sha
    path = repo / _LARGE
    path.write_text(_lines(10, label="inserted") + path.read_text())
    h2 = _commit(repo, "insert above X")
    # Round 2 read nothing of large.py: X is carried at h1's line 81.
    s2 = advance_review_state(
        request=StickyRequest(
            result=replace(
                first,
                findings=(),
                metadata=replace(
                    first.metadata,
                    reviewed_paths=("other.py",),
                    reviewed_ranges=(),
                    delta_since=h1,
                ),
            ),
            prior_state=s1,
            head_sha=h2,
        ),
    )
    assert_that((s2.findings[0].line, s2.findings[0].line_sha)).is_equal_to((81, h1))
    _edit_line(path, 90, "fixed_x = True\n")
    _commit(repo, "fix X at its current line 91")

    third, s3, body = _review(repo=repo, base=base, prior=s2, reported=[])

    assert_that(third.metadata.reviewed_ranges).contains((_LARGE, 88, 94))
    (x,) = s3.findings
    assert_that(x.status).is_equal_to(FindingStatus.RESOLVED)
    assert_that(x.line).is_equal_to(91)
    assert_that(s3.runs[-1].outcome.verdict).is_equal_to(ReviewVerdict.READY)
    assert_that(body).contains("~~X~~")
    assert_that(body).does_not_contain("⏸ not re-verified")


def test_a_carried_unread_finding_is_persisted_at_its_mapped_line(
    tmp_path: Path,
) -> None:
    """A finding carried again is stored at the base's coordinates, not h1's."""
    repo = tmp_path / "repo"
    base, h1 = _repo(repo, [_LARGE])
    path = repo / _LARGE
    path.write_text(_lines(10, label="inserted") + path.read_text())
    h2 = _commit(repo, "insert above X")
    _edit_line(path, 250, "far = True\n")
    h3 = _commit(repo, "far edit")
    prior = _x_carried(path=_LARGE, line=81, line_sha=h1, since=h2)
    plan = plan_delta(
        prior=prior,
        context=_context(repo=repo, base=base, head=h3, paths=[_LARGE]),
        repo_root=str(repo),
        force_full=False,
    )
    result = _relocated(
        result=_round_result(reviewed_paths=("other.py",), eligible=1),
        plan=plan,
    )

    after = advance_review_state(
        request=_sticky_request(prior=prior, result=result, head=h3),
    )

    (x,) = after.findings
    assert_that((x.line, x.line_sha, x.unverified)).is_equal_to((91, h2, True))
    assert_that(x.status).is_equal_to(FindingStatus.OPEN)
