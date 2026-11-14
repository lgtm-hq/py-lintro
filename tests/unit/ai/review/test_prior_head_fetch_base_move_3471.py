"""A head fetched across a moved base must not resolve a live finding (#3471).

Fetching the head an earlier round reviewed lets git place a prior finding
at the new head. When the base absorbed the finding's line in between, that
place is base content outside the pull request's diff: the provider's
re-report there is dropped by the diff gate and the matcher would resolve
the finding while the bug is live. Before the fetch the finding was held as
``"unmapped"``; with it, the base-move hold (#3467) is what keeps it open.
These tests require that hold.

The layout is the workflow's: the base branch at depth 1, then the PR head
fetched the way ``fetch_pr_head`` does, then the round. Every round runs
through ``run_review`` with the #3413 harness, in delta and full mode.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.ai.review.models.delta_plan import LOCATION_BASE_MOVED
from lintro.ai.review.sticky.state import matcher_relocations
from tests.unit.ai.review import test_full_round_rename_3413 as harness
from tests.unit.ai.review.scratch_repo_fixtures import commit, git, lines
from tests.unit.ai.review.test_prior_head_fetch_3471 import _ci_clone, _holds

_MODES = [
    pytest.param(False, id="delta"),
    pytest.param(True, id="full"),
]

_BUG = "can_read = lambda is_admin: is_admin or True\n"


@pytest.mark.parametrize("force_full", _MODES)
@pytest.mark.parametrize(
    ("first_path", "rebased_path"),
    [
        pytest.param("b.py", "c.py", id="renamed-again"),
        pytest.param("a.py", "a.py", id="same-path"),
    ],
)
def test_a_line_the_base_absorbed_is_held_after_the_fetch(
    tmp_path: Path,
    force_full: bool,
    first_path: str,
    rebased_path: str,
) -> None:
    """The base takes X's line; the rebased PR only appends a comment.

    ``renamed-again`` is the review's reproducer: round one sees X on
    ``b.py`` after ``a.py -> b.py``, and the rebased PR renames ``a.py ->
    c.py`` instead, which git follows from the fetched old head. Without the
    base-move hold the round resolved X's thread in both modes.
    """
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q", "-b", "main")
    git(source, "config", "uploadpack.allowAnySHA1InWant", "true")
    (source / "a.py").write_text(lines(300, label="base"))
    clean = commit(source, "clean base")
    text = lines(300, label="base").splitlines(keepends=True)
    text[80] = _BUG
    if first_path != "a.py":
        git(source, "mv", "a.py", first_path)
    (source / first_path).write_text("".join(text))
    old_head = commit(source, "PR adds the bug")
    first = harness._round(
        repo=source,
        base=clean,
        prior=None,
        reported=[harness._x(file=first_path)],
        force_full=force_full,
    )
    prior = harness._with_thread(first.state)
    key = prior.findings[0].key
    git(source, "reset", "-q", "--hard", clean)
    (source / "a.py").write_text("".join(text))
    absorbed = commit(source, "the base absorbs the live bug")
    if rebased_path != "a.py":
        git(source, "mv", "a.py", rebased_path)
    (source / rebased_path).write_text("".join(text) + "# unrelated PR comment\n")
    head = commit(source, "rebased PR")
    git(source, "checkout", "-q", "-b", "next", absorbed)
    (source / "later.py").write_text("later = 1\n")
    tip = commit(source, "the base moves on")
    git(source, "checkout", "-q", "--detach", head)
    clone = _ci_clone(tmp_path=tmp_path, source=source, base_tip=tip, head=head)
    assert_that(_holds(clone, old_head)).is_false()
    context = harness._git_context(repo=clone, base=absorbed)

    with patch.object(
        harness,
        "_git_context",
        return_value=replace(context, base_ref=tip),
    ):
        second = harness._round(
            repo=clone,
            base=absorbed,
            prior=prior,
            reported=[harness._x(file=rebased_path)],
            force_full=force_full,
        )

    assert_that(_holds(clone, old_head)).is_true()
    assert_that(second.resolved_threads).is_empty()
    assert_that(second.counts).is_in((1, 0, 0), (1, 1, 0))
    assert_that(matcher_relocations(result=second.result)).is_equal_to(
        {key: (first_path, 81, LOCATION_BASE_MOVED)},
    )
    (carried,) = second.match.carried
    assert_that((carried.key, carried.inline_comment_id)).is_equal_to(
        (key, harness._THREAD_ID),
    )
