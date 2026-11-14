"""Verify before resolve: git never closes a moved finding's thread (#3466).

Five attempts read git's rename and copy output, or the pull request's path
metadata, to decide that a finding had moved and was now absent. Each could
be bent into a false resolve or a thread handed to a different finding. Now a
prior open finding whose location had to be inferred, and that no report
matched, resolves only when the provider, asked directly, says the issue is
gone.

Every scenario below re-creates one of the adversarial histories found
against those attempts (heuristic look-alike files at 63% and 67% similarity, a
recreated path, a live finding carried onto a modified destination, a reused
first path, two findings converging on one file, a rewritten shallow
history, a base rewrite) and runs its final round through ``run_review`` on a
real git repository, in delta and full mode, with a canned provider. Only a
canned "fixed" answer may resolve a thread; "still present", "unknown", an
error, a timeout or an off-schema answer leave every thread open on the
finding it was opened for.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.providers.response import AIResponse
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.orchestrator import run_review
from lintro.ai.review.output import review_result_to_json
from lintro.ai.review.session import ReviewSessionOptions
from lintro.config.review_config import ReviewConfig, ReviewVerifyMode
from tests.unit.ai.review import test_full_round_rename_3413 as harness
from tests.unit.ai.review.scratch_repo_fixtures import (
    commit,
    fake_provider,
    git,
    lines,
    moved_answer,
    routed_calls,
)
from tests.unit.ai.review.test_base_move_hold_3467 import (
    _BUG,
    _rebase_and_rename,
    _reported_on_b,
)
from tests.unit.ai.review.test_chained_rename_lineage_3432 import (
    _carried_to_b,
    _shallow_clone,
)

_MODES = [
    pytest.param(False, id="delta"),
    pytest.param(True, id="full"),
]

_THREAD = harness._THREAD_ID


@dataclass(frozen=True)
class _Final:
    """The last round of a scenario, ready to run.

    Attributes:
        repo: The repository at the round's head.
        base: The base commit.
        prior: State carried into the round.
        reported: What the provider reports this round.
        threads: Thread id to the key of the finding it was opened for.
        live: ``file`` where the original defect still sits, at line 81.
    """

    repo: Path
    base: str
    prior: ReviewState
    reported: list[dict[str, Any]]
    threads: dict[int, str]
    live: str


def _bug(text: str) -> str:
    """Put the live defect at line 81.

    Args:
        text: File text of at least 81 lines.

    Returns:
        The text with line 81 replaced.
    """
    rows = text.splitlines(keepends=True)
    rows[80] = "bug_at_81 = True\n"
    return "".join(rows)


def _heuristic(tmp_path: Path, force_full: bool, *, cut: int) -> _Final:
    """A recreated ``a.py`` similar to ``b.py`` reads as ``b.py -> c.py``.

    ``cut`` lines of the recreated file match ``b.py`` (200 gives git's R063,
    212 its R067); the live ``b.py`` is renamed to ``d.py`` and rewritten.

    Args:
        tmp_path: Pytest temp dir.
        force_full: ``--full`` for every round.
        cut: Lines the look-alike shares with ``b.py``.

    Returns:
        The final round.
    """
    repo, base, prior = harness._first_round(tmp_path)
    key = prior.findings[0].key
    git(repo, "mv", "a.py", "b.py")
    commit(repo, "rename A B")
    one = harness._round(
        repo=repo,
        base=base,
        prior=prior,
        reported=[harness._x(file="b.py")],
        force_full=force_full,
    )
    copied = (
        lines(300, label="case").splitlines(keepends=True)[:cut]
        + lines(300, label="other").splitlines(keepends=True)[cut:]
    )
    (repo / "a.py").write_text("".join(copied))
    commit(repo, "unrelated recreated A similar to B")
    git(repo, "mv", "a.py", "c.py")
    git(repo, "mv", "b.py", "d.py")
    (repo / "d.py").write_text(_bug(lines(300, label="rewritten")))
    commit(repo, "rename reused A C and live B D")
    return _Final(
        repo=repo,
        base=base,
        prior=one.state,
        reported=[harness._x(file="d.py")],
        threads={_THREAD: key},
        live="d.py",
    )


def _recreated_path(tmp_path: Path, force_full: bool) -> _Final:
    """A weak rename's old path is recreated; the look-alike there is reported.

    Args:
        tmp_path: Pytest temp dir.
        force_full: ``--full`` for every round.

    Returns:
        The final round: X reported only on the recreated, unrelated ``a.py``
        while the original defect is still on ``b.py``.
    """
    repo, base, prior = harness._first_round(tmp_path)
    key = prior.findings[0].key
    git(repo, "mv", "a.py", "b.py")
    (repo / "b.py").write_text(lines(300, label="case") + lines(100, label="tail"))
    commit(repo, "weak rename with the bug untouched at b81")
    two = harness._round(
        repo=repo,
        base=base,
        prior=prior,
        reported=[harness._x(file="b.py")],
        force_full=force_full,
    )
    (repo / "a.py").write_text(lines(300, label="unrelated"))
    commit(repo, "recreate the old path with unrelated content")
    return _Final(
        repo=repo,
        base=base,
        prior=two.state,
        reported=[harness._x(file="a.py")],
        threads={_THREAD: key},
        live="b.py",
    )


def _carried_modified_destination(tmp_path: Path, force_full: bool) -> _Final:
    """A carried finding's file overwrites an existing file; a clean look-alike is added.

    Args:
        tmp_path: Pytest temp dir.
        force_full: ``--full`` for every round.

    Returns:
        The final round: the live defect on ``d.py``, clean ``c.py`` similar
        to the original ``a.py``.
    """
    base_text = lines(300, label="base").splitlines(keepends=True)
    case_text = lines(300, label="case").splitlines(keepends=True)
    heavy_text = lines(300, label="heavy").splitlines(keepends=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "a.py").write_text("".join(base_text))
    (repo / "d.py").write_text(lines(300, label="existing"))
    base = commit(repo, "base has A and an independent D")
    (repo / "a.py").write_text(_bug("".join(case_text[:100] + base_text[100:])))
    commit(repo, "round one introduces X on A")
    one = harness._round(
        repo=repo,
        base=base,
        prior=None,
        reported=[harness._x(file="a.py")],
        force_full=force_full,
    )
    prior = harness._with_thread(one.state)
    git(repo, "mv", "a.py", "b.py")
    commit(repo, "plain A B rename")
    two = harness._round(
        repo=repo,
        base=base,
        prior=prior,
        reported=[harness._x(file="b.py")],
        force_full=force_full,
    )
    git(repo, "mv", "-f", "b.py", "d.py")
    (repo / "d.py").write_text(_bug(lines(300, label="rewritten")))
    (repo / "c.py").write_text("".join(base_text[:170] + heavy_text[170:]))
    commit(repo, "live B overwrites D, clean C added")
    return _Final(
        repo=repo,
        base=base,
        prior=two.state,
        reported=[harness._x(file="d.py")],
        threads={_THREAD: prior.findings[0].key},
        live="d.py",
    )


def _reuse(tmp_path: Path, force_full: bool) -> _Final:
    """A new ``a.py`` is created and renamed to ``c.py`` between two reviews.

    Args:
        tmp_path: Pytest temp dir.
        force_full: ``--full`` for every round.

    Returns:
        The final round: the live defect on ``d.py`` (the renamed ``b.py``).
    """
    repo, base, state, key = _carried_to_b(tmp_path, force_full=force_full)
    (repo / "a.py").write_text(lines(300, label="base"))
    commit(repo, "re-create an unrelated a.py")
    git(repo, "mv", "a.py", "c.py")
    git(repo, "mv", "b.py", "d.py")
    (repo / "d.py").write_text(_bug(lines(300, label="rewritten")))
    commit(repo, "rename the new a.py, rename and rewrite b.py")
    return _Final(
        repo=repo,
        base=base,
        prior=state,
        reported=[harness._x(file="d.py")],
        threads={_THREAD: key},
        live="d.py",
    )


def _convergence(tmp_path: Path, force_full: bool) -> _Final:
    """X on ``b.py`` (from ``a.py``) and on ``e.py`` (from ``d.py``) meet on ``c.py``.

    Args:
        tmp_path: Pytest temp dir.
        force_full: ``--full`` for every round.

    Returns:
        The final round, two threads open.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "a.py").write_text(lines(300, label="base"))
    (repo / "d.py").write_text(lines(300, label="otherbase"))
    base = commit(repo, "two originals")
    (repo / "a.py").write_text(lines(300, label="case"))
    (repo / "d.py").write_text(_bug(lines(300, label="base")))
    commit(repo, "the same finding on two files")
    first = harness._round(
        repo=repo,
        base=base,
        prior=None,
        reported=[harness._x(file="a.py"), harness._x(file="d.py")],
        force_full=force_full,
    )
    threaded = replace(
        first.state,
        findings=tuple(
            replace(record, inline_comment_id=_THREAD + index)
            for index, record in enumerate(first.state.findings)
        ),
    )
    git(repo, "mv", "a.py", "b.py")
    git(repo, "mv", "d.py", "e.py")
    commit(repo, "rename both")
    second = harness._round(
        repo=repo,
        base=base,
        prior=threaded,
        reported=[harness._x(file="b.py"), harness._x(file="e.py")],
        force_full=force_full,
    )
    git(repo, "rm", "-q", "b.py")
    git(repo, "mv", "e.py", "c.py")
    commit(repo, "remove b.py, rename e.py to c.py")
    return _Final(
        repo=repo,
        base=base,
        prior=second.state,
        reported=[harness._x(file="c.py")],
        threads={
            record.inline_comment_id: record.key
            for record in threaded.findings
            if record.inline_comment_id is not None
        },
        live="c.py",
    )


def _shallow(tmp_path: Path, force_full: bool) -> _Final:
    """The reviewed head is gone from a depth-1 clone of a rewritten branch.

    Args:
        tmp_path: Pytest temp dir.
        force_full: ``--full`` for the final round.

    Returns:
        The final round, run in the shallow clone.
    """
    (tmp_path / "source").mkdir()
    repo, base, state, key = _carried_to_b(tmp_path / "source", force_full=False)
    git(repo, "reset", "-q", "--hard", base)
    git(repo, "mv", "a.py", "c.py")
    (repo / "c.py").write_text(_bug(lines(300, label="base")))
    commit(repo, "rewritten feature, renamed to c.py")
    clone = _shallow_clone(tmp_path=tmp_path, repo=repo, base=base)
    return _Final(
        repo=clone,
        base=base,
        prior=state,
        reported=[harness._x(file="c.py")],
        threads={_THREAD: key},
        live="c.py",
    )


def _base_rewrite(tmp_path: Path, force_full: bool) -> _Final:
    """The base advances past the bug; the gate drops its re-report.

    Args:
        tmp_path: Pytest temp dir.
        force_full: Unused: the scenario's first round is fixed.

    Returns:
        The final round, X still live on ``c.py``.
    """
    del force_full
    repo, _, prior = _reported_on_b(tmp_path)
    new_base = _rebase_and_rename(repo, line_81=_BUG)
    return _Final(
        repo=repo,
        base=new_base,
        prior=prior,
        reported=[harness._x(file="c.py")],
        threads={_THREAD: prior.findings[0].key},
        live="c.py",
    )


def _replaced_path(tmp_path: Path, force_full: bool) -> _Final:
    """The live bug moves into a rewritten ``d.py``; ``a.py`` is refilled.

    Git reads the history as ``M100 a.py`` plus ``A d.py``: the stored path
    still exists, so nothing reports a rename, yet the code there is gone.

    Args:
        tmp_path: Pytest temp dir.
        force_full: ``--full`` for every round.

    Returns:
        The final round: X reported on ``d.py``, where the bug now lives.
    """
    repo, base, _ = harness._first_round(tmp_path)
    text = (repo / "a.py").read_text()
    (repo / "a.py").write_text(
        text.replace("case_81 = 81", "can_read = lambda is_admin: is_admin or True"),
    )
    commit(repo, "introduce an executable authorization bug")
    first = harness._round(
        repo=repo,
        base=base,
        prior=None,
        reported=[harness._x(file="a.py")],
        force_full=force_full,
    )
    prior = harness._with_thread(first.state)
    reviewed = git(repo, "rev-parse", "HEAD")
    git(repo, "mv", "a.py", "d.py")
    (repo / "d.py").write_text(
        lines(300, label="rewritten").replace(
            "rewritten_81 = 81",
            "can_read = lambda is_admin: is_admin or True",
        ),
    )
    (repo / "a.py").write_text(lines(300, label="unrelated"))
    commit(repo, "move the live bug and refill the stored path")
    status = git(repo, "diff", "-M", "-B", "--name-status", f"{reviewed}..HEAD")
    assert_that(status).contains("M100\ta.py").contains("A\td.py")
    return _Final(
        repo=repo,
        base=base,
        prior=prior,
        reported=[harness._x(file="d.py")],
        threads={_THREAD: prior.findings[0].key},
        live="d.py",
    )


_SCENARIOS: dict[str, Callable[[Path, bool], _Final]] = {
    "heuristic": lambda tmp, full: _heuristic(tmp, full, cut=200),
    "heuristic67": lambda tmp, full: _heuristic(tmp, full, cut=212),
    "recreated-path": _recreated_path,
    "carried-modified-destination": _carried_modified_destination,
    "reuse": _reuse,
    "convergence": _convergence,
    "shallow": _shallow,
    "base-rewrite": _base_rewrite,
    "replaced-path": _replaced_path,
}


def _answers(live: str) -> dict[str, AIResponse | Exception | None]:
    """Every answer that must hold, by name.

    Args:
        live: The file the defect still sits on.

    Returns:
        The canned answers (an exception is raised by the provider call).
    """
    return {
        "still-present": moved_answer(still_present="true"),
        "still-present-at-the-live-copy": moved_answer(
            still_present="true",
            file=live,
            line=81,
        ),
        "unknown": moved_answer(still_present="unknown"),
        "provider-error": RuntimeError("provider unavailable"),
        "timeout": TimeoutError("provider timed out"),
        "off-schema": None,
    }


_HOLDING_ANSWERS = [
    "still-present",
    "still-present-at-the-live-copy",
    "unknown",
    "provider-error",
    "timeout",
    "off-schema",
]


def _assert_threads_kept(*, round_: harness._Round, final: _Final) -> None:
    """No thread resolved, and each is still on the finding it was opened for.

    Args:
        round_: The final round.
        final: The scenario.
    """
    assert_that(round_.resolved_threads).is_empty()
    assert_that(round_.match.resolved).is_empty()
    for thread, key in final.threads.items():
        holders = [
            record
            for record in round_.state.findings
            if record.inline_comment_id == thread
        ]
        assert_that(holders).is_length(1)
        (holder,) = holders
        assert_that((holder.key, holder.status)).is_equal_to(
            (key, FindingStatus.OPEN),
        )


@pytest.mark.parametrize("answer", _HOLDING_ANSWERS)
@pytest.mark.parametrize("force_full", _MODES)
@pytest.mark.parametrize("scenario", list(_SCENARIOS))
def test_no_answer_but_fixed_resolves_or_moves_a_thread(
    tmp_path: Path,
    scenario: str,
    force_full: bool,
    answer: str,
) -> None:
    """Still present, unknown, an error, a timeout or an off-schema reply hold.

    Args:
        tmp_path: Pytest temp dir.
        scenario: The adversarial history.
        force_full: ``--full``.
        answer: The canned answer to the moved-finding check.
    """
    final = _SCENARIOS[scenario](tmp_path, force_full)

    round_ = harness._round(
        repo=final.repo,
        base=final.base,
        prior=final.prior,
        reported=final.reported,
        force_full=force_full,
        moved=_answers(final.live)[answer],
    )

    _assert_threads_kept(round_=round_, final=final)
    summary = round_.result.metadata.moved_verification
    assert summary is not None
    assert_that(summary.candidates).is_greater_than_or_equal_to(1)
    assert_that(summary.verified_fixed).is_zero()
    assert_that(round_.sticky).contains(f"Re-verified {summary.candidates} moved")


@pytest.mark.parametrize("force_full", _MODES)
@pytest.mark.parametrize("scenario", list(_SCENARIOS))
def test_only_a_fixed_answer_resolves(
    tmp_path: Path,
    scenario: str,
    force_full: bool,
) -> None:
    """The provider's "fixed" closes exactly the findings that needed the check.

    The original moved finding always needs it. In the convergence history
    the full round also matches the second finding to its ``c.py`` report,
    so that one is not asked about and stays open.

    Args:
        tmp_path: Pytest temp dir.
        scenario: The adversarial history.
        force_full: ``--full``.
    """
    final = _SCENARIOS[scenario](tmp_path, force_full)

    round_ = harness._round(
        repo=final.repo,
        base=final.base,
        prior=final.prior,
        reported=final.reported,
        force_full=force_full,
        moved=moved_answer(still_present="false"),
    )

    asked = {entry[0] for entry in round_.result.metadata.moved_verdicts}
    resolved = {record.key: record for record in round_.match.resolved}
    assert_that(set(resolved)).is_equal_to(asked)
    assert_that(resolved).contains_key(final.threads[_THREAD])
    assert_that(round_.resolved_threads).contains(f"T{_THREAD}")
    assert_that(len(round_.resolved_threads)).is_equal_to(
        sum(1 for record in resolved.values() if record.inline_comment_id),
    )
    summary = round_.result.metadata.moved_verification
    assert summary is not None
    assert_that((summary.verified_fixed, summary.held)).is_equal_to((len(asked), 0))
    assert_that(round_.sticky).contains(f"{len(asked)} verified fixed")


def _renamed_with_edit(tmp_path: Path) -> tuple[Path, str, ReviewState]:
    """Rename ``a.py -> b.py`` and edit next to line 81, keeping X's code.

    Args:
        tmp_path: Pytest temp dir.

    Returns:
        The repository, the base commit and the state after round one.
    """
    repo, base, prior = harness._first_round(tmp_path)
    git(repo, "mv", "a.py", "b.py")
    rows = (repo / "b.py").read_text().splitlines(keepends=True)
    rows[79] = "edited_80 = 80\n"
    (repo / "b.py").write_text("".join(rows))
    commit(repo, "rename and edit next to X")
    return repo, base, prior


@pytest.mark.parametrize("force_full", _MODES)
def test_a_present_answer_inside_the_diff_moves_the_finding_with_its_thread(
    tmp_path: Path,
    force_full: bool,
) -> None:
    """Unreported after a rename, X is placed where the provider says it is.

    Args:
        tmp_path: Pytest temp dir.
        force_full: ``--full``.
    """
    repo, base, prior = _renamed_with_edit(tmp_path)
    (original,) = prior.findings

    round_ = harness._round(
        repo=repo,
        base=base,
        prior=prior,
        reported=[],
        force_full=force_full,
        moved=moved_answer(still_present="true", file="b.py", line=80),
    )

    assert_that(round_.counts).is_equal_to((1, 0, 0))
    assert_that(round_.resolved_threads).is_empty()
    (record,) = round_.state.findings
    assert_that(
        (record.key, record.file, record.line, record.inline_comment_id),
    ).is_equal_to((original.key, "b.py", 80, _THREAD))
    assert_that((record.status, record.unverified)).is_equal_to(
        (FindingStatus.OPEN, False),
    )
    summary = round_.result.metadata.moved_verification
    assert summary is not None
    assert_that((summary.verified_present, summary.moved)).is_equal_to((1, 1))


@pytest.mark.parametrize(
    ("file", "line"),
    [
        pytest.param("b.py", 400, id="past-the-end-of-the-file"),
        pytest.param("nowhere.py", 80, id="not-in-the-diff"),
        pytest.param("", 0, id="no-location"),
    ],
)
def test_a_present_answer_outside_the_diff_holds_in_place(
    tmp_path: Path,
    file: str,
    line: int,
) -> None:
    """A location the diff does not cover is ignored: X stays open, unmoved.

    Args:
        tmp_path: Pytest temp dir.
        file: The file the answer names.
        line: The line it names.
    """
    repo, base, prior = _renamed_with_edit(tmp_path)
    (original,) = prior.findings

    round_ = harness._round(
        repo=repo,
        base=base,
        prior=prior,
        reported=[],
        force_full=True,
        moved=moved_answer(still_present="true", file=file, line=line),
    )

    (record,) = round_.state.findings
    assert_that((record.key, record.status, record.unverified)).is_equal_to(
        (original.key, FindingStatus.OPEN, False),
    )
    assert_that(record.line).is_not_equal_to(line)
    assert_that(round_.resolved_threads).is_empty()
    summary = round_.result.metadata.moved_verification
    assert summary is not None
    assert_that((summary.verified_present, summary.moved)).is_equal_to((1, 0))


def _run_with(
    *,
    repo: Path,
    base: str,
    prior: ReviewState,
    moved: AIResponse | Exception | None,
    verify_moved_findings: bool = True,
    context_window: int | None = None,
) -> tuple[Any, list[str]]:
    """Run one round with the check configured, recording its prompts.

    Args:
        repo: The repository.
        base: The base commit.
        prior: State carried into the round.
        moved: The moved-finding check's canned answer.
        verify_moved_findings: The config switch.
        context_window: A context window to force, with the smallest
            synthesis budget, so the check's material cannot fit.

    Returns:
        The result and the user prompt of every moved-finding check.
    """
    seen: list[str] = []
    with patch(
        "lintro.ai.review.provider_call.call_ai",
        side_effect=routed_calls(review=harness._canned([]), moved=moved, seen=seen),
    ):
        result = run_review(
            harness._git_context(repo=repo, base=base),
            options=ReviewSessionOptions(
                provider=fake_provider(),
                ai_config=AIConfig(
                    enabled=True,
                    review=True,
                    review_generated_questions=False,
                    transport=AITransport.API,
                    review_synthesis_diff_tokens=1_000,
                ),
                checklist_items=[],
                checklist_text="",
                classifications=[],
                context_window_override=context_window,
                prior_state=prior,
                enforce_cost_cap=False,
                verify=ReviewVerifyMode.OFF,
                verify_moved_findings=verify_moved_findings,
            ),
        )
    return result, seen


def test_turning_the_check_off_holds_without_a_call(tmp_path: Path) -> None:
    """``verify_moved_findings: false`` makes no call and resolves nothing.

    Args:
        tmp_path: Pytest temp dir.
    """
    repo, base, prior = _renamed_with_edit(tmp_path)

    result, seen = _run_with(
        repo=repo,
        base=base,
        prior=prior,
        moved=moved_answer(still_present="false"),
        verify_moved_findings=False,
    )

    assert_that(seen).is_empty()
    summary = result.metadata.moved_verification
    assert summary is not None
    assert_that(summary.to_dict()).contains_entry({"enabled": False}).contains_entry(
        {"held": 1},
    ).contains_entry({"verified_fixed": 0})
    assert_that(ReviewConfig().verify_moved_findings).is_true()
    switched_off = ReviewConfig(verify_moved_findings=False)
    assert_that(switched_off.verify_moved_findings).is_false()


def test_the_check_reads_the_original_finding_the_diff_and_the_files(
    tmp_path: Path,
) -> None:
    """One call carries X's record and code, the diff and ``b.py``'s content.

    Args:
        tmp_path: Pytest temp dir.
    """
    repo, base, prior = _renamed_with_edit(tmp_path)
    (original,) = prior.findings

    _result, seen = _run_with(
        repo=repo,
        base=base,
        prior=prior,
        moved=moved_answer(still_present="false"),
    )

    (prompt,) = seen
    assert_that(prompt).contains(
        "title: X",
        f"fingerprint {original.fingerprint}",
        "reported at: a.py:81",
        "81: case_81 = 81",
        "diff --git",
        'file: "b.py"',
    )
    # Once in the diff, once in the file's current content.
    assert_that(prompt.count("edited_80 = 80")).is_equal_to(2)


def test_the_run_json_reports_the_counts(tmp_path: Path) -> None:
    """The run JSON carries verified fixed, verified present and held.

    Args:
        tmp_path: Pytest temp dir.
    """
    repo, base, prior = _renamed_with_edit(tmp_path)

    result, _seen = _run_with(
        repo=repo,
        base=base,
        prior=prior,
        moved=moved_answer(still_present="false"),
    )

    payload = json.loads(review_result_to_json(result=result))
    assert_that(payload["moved_verification"]).contains_entry(
        {"candidates": 1},
        {"verified_fixed": 1},
        {"verified_present": 0},
        {"held": 0},
        {"failed": False},
    )
    assert_that(payload["moved_verification"]["cost_estimate"]).is_greater_than(0)


@pytest.mark.parametrize("force_full", _MODES)
def test_an_edited_line_is_not_in_place(tmp_path: Path, force_full: bool) -> None:
    """X's own line rewritten: it waits for the check, and "fixed" resolves it.

    Args:
        tmp_path: Pytest temp dir.
        force_full: ``--full``.
    """
    repo, base, prior = harness._first_round(tmp_path)
    rows = (repo / "a.py").read_text().splitlines(keepends=True)
    rows[80] = "fixed_81 = 81\n"
    (repo / "a.py").write_text("".join(rows))
    commit(repo, "rewrite X's line")

    held = harness._round(
        repo=repo,
        base=base,
        prior=prior,
        reported=[],
        force_full=force_full,
    )
    fixed = harness._round(
        repo=repo,
        base=base,
        prior=prior,
        reported=[],
        force_full=force_full,
        moved=moved_answer(still_present="false"),
    )

    assert_that((held.counts, held.resolved_threads)).is_equal_to(((1, 0, 0), ()))
    assert_that(held.state.findings[0].unverified).is_true()
    assert_that(fixed.counts).is_equal_to((0, 0, 1))
    assert_that(fixed.resolved_threads).is_equal_to((f"T{_THREAD}",))


def _big_renamed_twice(tmp_path: Path) -> tuple[Path, str, ReviewState]:
    """X on a 140 KB ``a.py`` whose two renamed copies differ only in the fix.

    Args:
        tmp_path: Pytest temp dir.

    Returns:
        The repository, the base commit and the state after round one.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "a.py").write_text(lines(5000, label="base"))
    base = commit(repo, "base")
    body = lines(5000, label="case").splitlines(keepends=True)
    body[80] = "value = eval(user_input)\n"
    (repo / "a.py").write_text("".join(body))
    commit(repo, "eval on user input")
    first = harness._round(
        repo=repo,
        base=base,
        prior=None,
        reported=[harness._x(file="a.py")],
        force_full=False,
    )
    git(repo, "mv", "a.py", "b.py")
    (repo / "c.py").write_text("".join(body))
    body[80] = "value = int(user_input)\n"
    (repo / "b.py").write_text("".join(body))
    commit(repo, "fix one copy, keep the eval in another")
    return repo, base, harness._with_thread(first.state)


def test_a_fixed_answer_over_a_cut_diff_holds(tmp_path: Path) -> None:
    """The diff and files do not fit: "fixed" may not resolve X.

    The live ``eval`` copy is past the cut, so the provider never saw it.

    Args:
        tmp_path: Pytest temp dir.
    """
    repo, base, prior = _big_renamed_twice(tmp_path)

    result, seen = _run_with(
        repo=repo,
        base=base,
        prior=prior,
        moved=moved_answer(still_present="false"),
        context_window=8_000,
    )

    (prompt,) = seen
    assert_that(prompt).contains(
        "[diff truncated]",
        "not shown: over the prompt budget",
    )
    summary = result.metadata.moved_verification
    assert summary is not None
    assert_that(summary.to_dict()).contains_entry(
        {"verified_fixed": 0},
        {"held": 1},
        {"incomplete": True},
    )
    verdicts = {entry[0]: entry[1] for entry in result.metadata.moved_verdicts}
    assert_that(verdicts).is_equal_to({prior.findings[0].key: "unknown"})


@pytest.mark.parametrize("force_full", _MODES)
def test_an_in_place_finding_resolves_without_the_check(
    tmp_path: Path,
    force_full: bool,
) -> None:
    """X on an untouched line, no longer reported, resolves as before.

    Its line maps exactly to the new head (the fix lands elsewhere in the
    file), so the round needs no extra call.

    Args:
        tmp_path: Pytest temp dir.
        force_full: ``--full``.
    """
    repo, base, prior = harness._first_round(tmp_path)
    rows = (repo / "a.py").read_text().splitlines(keepends=True)
    rows[199] = "guard_200 = True\n"
    (repo / "a.py").write_text("".join(rows))
    commit(repo, "fix X with a guard further down")

    # Were the check consulted, its "still present" would keep X open.
    round_ = harness._round(
        repo=repo,
        base=base,
        prior=prior,
        reported=[],
        force_full=force_full,
        moved=moved_answer(still_present="true"),
    )

    assert_that(round_.counts).is_equal_to((0, 0, 1))
    assert_that(round_.resolved_threads).is_equal_to((f"T{_THREAD}",))
    assert_that(round_.result.metadata.moved_verification).is_none()
    assert_that(round_.sticky).does_not_contain("Re-verified")
