"""A shallow CI checkout fetches the heads earlier rounds reviewed (#3471).

The dogfood workflow checks the trusted base out at depth 1, and the review
fetches the pull request's head. After a rebase the head an earlier round
reviewed is in neither, so git cannot map that round's findings forward:
they are held as ``"unmapped"``. The base tip is a shallow boundary too, so
``merge-base(base, head)`` is unknown.

These tests rebuild that layout from a scratch origin: a depth-1 fetch of
the base branch, then ``refs/pull/1/head`` the way ``fetch_pr_head`` fetches
it, then the round itself. The origin serves commits by id as GitHub does
(``uploadpack.allowAnySHA1InWant``) until a garbage collection drops the
force-pushed head. Every round runs through ``run_review`` with the #3413
harness.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.ai.review import prior_head_fetch
from lintro.ai.review.context import git_ops
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.review_checkout import ReviewCheckout
from lintro.ai.review.enums.review_context_error_code import ReviewContextErrorCode
from lintro.ai.review.exceptions import ReviewContextError
from lintro.ai.review.models.delta_plan import LOCATION_UNMAPPED
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.pr_metadata import PRMetadata
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.prior_head_fetch import (
    BASE_DEEPEN_STEPS,
    MAX_PRIOR_HEADS,
    NO_PRIOR_FETCH_ENV,
    fetch_prior_heads,
    prior_heads,
)
from lintro.ai.review.sticky.state import matcher_relocations
from tests.unit.ai.review import test_full_round_rename_3413 as harness
from tests.unit.ai.review.scratch_repo_fixtures import commit, git

_MODES = [
    pytest.param(False, id="delta"),
    pytest.param(True, id="full"),
]

#: Lines the rebased head inserts above X, so X moves from 81 to 86.
_SHIFT = 5


@dataclass(frozen=True)
class _Rebased:
    """A pull request reviewed once, then rebased onto a base that moved on.

    Attributes:
        source: The origin repository.
        clone: The CI-shaped checkout at the rebased head.
        merge_base: The commit the rebased head sits on.
        base_tip: The base branch tip, the clone's depth-1 commit.
        old_head: The head round one reviewed, force-pushed away.
        state: The state after round one.
        key: X's key.
    """

    source: Path
    clone: Path
    merge_base: str
    base_tip: str
    old_head: str
    state: ReviewState
    key: str


def _holds(repo: Path, sha: str) -> bool:
    """Tell whether a repository holds an object, reachable or not.

    Args:
        repo: The repository.
        sha: The object id.

    Returns:
        True when the object is in the repository's object store.
    """
    listed = git(repo, "cat-file", "--batch-all-objects", "--batch-check=%(objectname)")
    return sha in listed.splitlines()


def _ci_clone(*, tmp_path: Path, source: Path, base_tip: str, head: str) -> Path:
    """Check out as the workflow does: base at depth 1, then the PR head.

    Args:
        tmp_path: Pytest temp dir; the clone goes in ``workflow``.
        source: The origin repository.
        base_tip: The base branch tip.
        head: The pull request head.

    Returns:
        The shallow clone, detached at the PR head.
    """
    git(source, "update-ref", "refs/heads/trusted", base_tip)
    git(source, "update-ref", "refs/pull/1/head", head)
    clone = tmp_path / "workflow"
    git(tmp_path, "init", "-q", str(clone))
    git(clone, "remote", "add", "origin", source.as_uri())
    git(clone, "fetch", "-q", "--no-tags", "--depth", "1", "origin", "trusted")
    git(clone, "checkout", "-q", "--detach", "FETCH_HEAD")
    git(
        clone,
        "fetch",
        "-q",
        "--no-tags",
        "origin",
        "+refs/pull/1/head:refs/lintro/pr/1",
    )
    git(clone, "checkout", "-q", "--detach", "refs/lintro/pr/1")
    assert_that(git(clone, "rev-parse", "--is-shallow-repository")).is_equal_to(
        "true",
    )
    return clone


def _rebased(
    tmp_path: Path,
    *,
    rename: bool = False,
    fix: bool = False,
    collected: bool = False,
) -> _Rebased:
    """Review X at ``a.py:81``, rebase the PR, move the base on, clone as CI.

    Args:
        tmp_path: Pytest temp dir.
        rename: The rebased head also renames ``a.py`` to ``c.py``.
        fix: The rebased head rewrites X's line.
        collected: The origin garbage-collects the force-pushed head.

    Returns:
        The scenario.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source, merge_base_0, state = harness._first_round(source_dir)
    key = state.findings[0].key
    old_head = git(source, "rev-parse", "HEAD")
    git(source, "config", "uploadpack.allowAnySHA1InWant", "true")
    git(source, "reset", "-q", "--hard", merge_base_0)
    (source / "moved.py").write_text("moved = 1\n")
    merge_base = commit(source, "the base moves")
    git(source, "checkout", "-q", old_head, "--", "a.py")
    text = (source / "a.py").read_text().splitlines(keepends=True)
    if fix:
        text[80] = "fixed_at_81 = True\n"
    text[:0] = [f"inserted_{index} = {index}\n" for index in range(_SHIFT)]
    (source / "a.py").write_text("".join(text))
    if rename:
        git(source, "mv", "a.py", "c.py")
    head = commit(source, "rebased feature")
    git(source, "checkout", "-q", "-b", "next", merge_base)
    (source / "later.py").write_text("later = 1\n")
    base_tip = commit(source, "the base moves on")
    git(source, "checkout", "-q", "--detach", head)
    if collected:
        git(source, "reflog", "expire", "--expire=now", "--all")
        git(source, "gc", "-q", "--prune=now")
        assert_that(_holds(source, old_head)).is_false()
    clone = _ci_clone(tmp_path=tmp_path, source=source, base_tip=base_tip, head=head)
    assert_that(_holds(clone, old_head)).is_false()
    return _Rebased(
        source=source,
        clone=clone,
        merge_base=merge_base,
        base_tip=base_tip,
        old_head=old_head,
        state=state,
        key=key,
    )


def _as_github_reports(scenario: _Rebased) -> AbstractContextManager[object]:
    """Serve the round GitHub's view: merge-base diff, base branch tip as base.

    Args:
        scenario: The scenario.

    Returns:
        A patch of the harness's context builder.
    """
    context = harness._git_context(repo=scenario.clone, base=scenario.merge_base)
    return patch.object(
        harness,
        "_git_context",
        return_value=replace(context, base_ref=scenario.base_tip),
    )


def _review(
    scenario: _Rebased,
    *,
    reported: list[dict[str, object]],
    force_full: bool,
) -> harness._Round:
    """Run round two in the CI clone.

    Args:
        scenario: The scenario.
        reported: Findings the provider returns.
        force_full: ``--full``.

    Returns:
        The round.
    """
    with _as_github_reports(scenario):
        return harness._round(
            repo=scenario.clone,
            base=scenario.merge_base,
            prior=scenario.state,
            reported=reported,
            force_full=force_full,
        )


@pytest.mark.parametrize("force_full", _MODES)
def test_a_force_pushed_head_is_fetched_and_its_finding_mapped(
    tmp_path: Path,
    force_full: bool,
) -> None:
    """X maps from the old head to ``a.py:86`` and keeps its thread."""
    scenario = _rebased(tmp_path)

    second = _review(
        scenario,
        reported=[harness._x(file="a.py", line=81 + _SHIFT)],
        force_full=force_full,
    )

    head = git(scenario.clone, "rev-parse", "HEAD")
    assert_that(matcher_relocations(result=second.result)).is_equal_to(
        {scenario.key: ("a.py", 81 + _SHIFT, head)},
    )
    assert_that(_holds(scenario.clone, scenario.old_head)).is_true()
    harness._assert_carried_with_thread(
        round_=second,
        key=scenario.key,
        file="a.py",
    )
    assert_that(second.match.carried[0].line).is_equal_to(81 + _SHIFT)
    assert_that(second.state.runs[-1].identity.merge_base).is_equal_to(
        scenario.merge_base,
    )


@pytest.mark.parametrize("force_full", _MODES)
def test_a_rename_on_the_rebased_head_maps_the_finding_to_its_line(
    tmp_path: Path,
    force_full: bool,
) -> None:
    """Git follows X's own path, ``a.py -> c.py``, with its line (#3448)."""
    scenario = _rebased(tmp_path, rename=True)

    second = _review(
        scenario,
        reported=[harness._x(file="c.py", line=81 + _SHIFT)],
        force_full=force_full,
    )

    head = git(scenario.clone, "rev-parse", "HEAD")
    assert_that(matcher_relocations(result=second.result)).is_equal_to(
        {scenario.key: ("c.py", 81 + _SHIFT, head)},
    )
    harness._assert_carried_with_thread(
        round_=second,
        key=scenario.key,
        file="c.py",
    )


@pytest.mark.parametrize("force_full", _MODES)
def test_a_finding_the_rebased_head_fixed_still_resolves(
    tmp_path: Path,
    force_full: bool,
) -> None:
    """X's line was rewritten and nothing reports X: it resolves."""
    scenario = _rebased(tmp_path, fix=True)

    second = _review(scenario, reported=[], force_full=force_full)

    assert_that(second.counts).is_equal_to((0, 0, 1))
    assert_that(second.resolved_threads).is_equal_to(
        (f"T{harness._THREAD_ID}",),
    )
    (x,) = second.state.findings
    assert_that(x.status).is_equal_to(FindingStatus.RESOLVED)


def test_a_collected_head_is_reported_and_its_finding_stays_unmapped(
    tmp_path: Path,
) -> None:
    """The origin no longer has the old head: the round runs, X is held."""
    scenario = _rebased(tmp_path, collected=True)

    second = _review(
        scenario,
        reported=[harness._x(file="a.py", line=81 + _SHIFT)],
        force_full=True,
    )

    assert_that(matcher_relocations(result=second.result)).is_equal_to(
        {scenario.key: ("a.py", 81, LOCATION_UNMAPPED)},
    )
    assert_that(second.resolved_threads).is_empty()
    # The base is still deepened, so the merge-base is known regardless.
    assert_that(second.state.runs[-1].identity.merge_base).is_equal_to(
        scenario.merge_base,
    )


def test_the_fetch_names_what_it_could_not_reach(tmp_path: Path) -> None:
    """A collected head comes back as unreachable; the base still deepens."""
    scenario = _rebased(tmp_path, collected=True)
    context = harness._git_context(repo=scenario.clone, base=scenario.merge_base)

    fetched = fetch_prior_heads(
        prior=scenario.state,
        context=replace(context, base_ref=scenario.base_tip),
        repo_root=str(scenario.clone),
    )

    assert_that(fetched.fetched).is_empty()
    assert_that(fetched.unreachable).is_equal_to((scenario.old_head,))
    assert_that(fetched.merge_base).is_equal_to(scenario.merge_base)


def test_a_complete_checkout_fetches_nothing(tmp_path: Path) -> None:
    """Every commit is already present: no fetch, and no origin is needed."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    repo, base, state = harness._first_round(source_dir)
    context = harness._git_context(repo=repo, base=base)

    fetched = fetch_prior_heads(prior=state, context=context, repo_root=str(repo))

    assert_that(fetched.fetched).is_empty()
    assert_that(fetched.unreachable).is_empty()
    assert_that(fetched.merge_base).is_equal_to(base)


def test_a_run_without_a_tree_fetches_nothing(tmp_path: Path) -> None:
    """No tree means no git to ask: the fetch is skipped outright."""
    context = ReviewContext(
        base_ref="a" * 40,
        head_ref="b" * 40,
        changed_files=[],
        unified_diff="",
        repo_root=str(tmp_path),
        checkout=ReviewCheckout.NONE,
        pr_metadata=PRMetadata(title="t", body="", number=1, repo="o/r"),
    )

    fetched = fetch_prior_heads(prior=None, context=context, repo_root=str(tmp_path))

    assert_that((fetched.fetched, fetched.unreachable, fetched.merge_base)).is_equal_to(
        ((), (), ""),
    )


def test_prior_heads_skip_holds_and_closed_findings_and_stop_at_the_cap() -> None:
    """Only full commit ids of open findings count, most findings first."""
    shas = [f"{index:040x}" for index in range(MAX_PRIOR_HEADS + 3)]
    findings = [
        FindingRecord(fingerprint="held", file="a.py", line_sha=LOCATION_UNMAPPED),
        FindingRecord(fingerprint="empty", file="a.py"),
        FindingRecord(
            fingerprint="closed",
            file="a.py",
            line_sha="c" * 40,
            status=FindingStatus.RESOLVED,
        ),
        FindingRecord(fingerprint="twice-1", file="b.py", line_sha=shas[-1]),
        FindingRecord(fingerprint="twice-2", file="b.py", line_sha=shas[-1]),
        *(
            FindingRecord(fingerprint=f"f{index}", file="a.py", line_sha=sha)
            for index, sha in enumerate(shas[:-1])
        ),
    ]

    heads = prior_heads(prior=ReviewState(findings=tuple(findings)))

    assert_that(heads).is_length(MAX_PRIOR_HEADS)
    assert_that(heads[0]).is_equal_to(shas[-1])
    assert_that(heads).does_not_contain(LOCATION_UNMAPPED, "", "c" * 40)


def test_the_off_switch_leaves_the_checkout_as_it_was(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``LINTRO_REVIEW_NO_PRIOR_FETCH=1``: nothing is fetched, X stays held."""
    scenario = _rebased(tmp_path)
    monkeypatch.setenv(NO_PRIOR_FETCH_ENV, "1")

    second = _review(
        scenario,
        reported=[harness._x(file="a.py", line=81 + _SHIFT)],
        force_full=True,
    )

    assert_that(_holds(scenario.clone, scenario.old_head)).is_false()
    assert_that(matcher_relocations(result=second.result)).is_equal_to(
        {scenario.key: ("a.py", 81, LOCATION_UNMAPPED)},
    )
    assert_that(second.state.runs[-1].identity.merge_base).is_empty()


def test_a_spent_budget_starts_no_fetch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no time left the missing head is unreachable and nothing deepens."""
    scenario = _rebased(tmp_path)
    monkeypatch.setattr(prior_head_fetch, "PRIOR_FETCH_BUDGET_SECONDS", 0.0)
    shallow_before = git(scenario.clone, "rev-parse", "--git-path", "shallow")
    boundary = (scenario.clone / shallow_before).read_text()
    context = harness._git_context(repo=scenario.clone, base=scenario.merge_base)

    fetched = fetch_prior_heads(
        prior=scenario.state,
        context=replace(context, base_ref=scenario.base_tip),
        repo_root=str(scenario.clone),
    )

    assert_that(fetched.unreachable).is_equal_to((scenario.old_head,))
    assert_that(fetched.merge_base).is_empty()
    assert_that((scenario.clone / shallow_before).read_text()).is_equal_to(boundary)


def test_a_timed_out_fetch_stops_before_deepening(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A batch that times out ends the preparation: no retry and no deepen."""
    scenario = _rebased(tmp_path)
    started: list[list[str]] = []

    def timing_out(
        *,
        args: list[str],
        check: bool = True,
        timeout: float = 120.0,
    ) -> object:
        if "fetch" in args:
            started.append(args)
            raise ReviewContextError(
                "git fetch timed out after 1s",
                code=ReviewContextErrorCode.GIT_COMMAND_FAILED,
            )
        return git_ops._run_git(args=args, check=check, timeout=timeout)

    monkeypatch.setattr(prior_head_fetch, "_run_git", timing_out)
    context = harness._git_context(repo=scenario.clone, base=scenario.merge_base)

    fetched = fetch_prior_heads(
        prior=scenario.state,
        context=replace(context, base_ref=scenario.base_tip),
        repo_root=str(scenario.clone),
    )

    assert_that(fetched.unreachable).is_equal_to((scenario.old_head,))
    assert_that(fetched.merge_base).is_empty()
    assert_that([arg for args in started for arg in args]).does_not_contain(
        *[f"--deepen={step}" for step in BASE_DEEPEN_STEPS],
    )
    assert_that(started).is_length(1)
