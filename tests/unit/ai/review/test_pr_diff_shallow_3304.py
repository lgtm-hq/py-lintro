"""PR-head shallow checkouts must establish ancestry before building a diff."""

from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.ai.review import preparation
from lintro.ai.review.context.collection import (
    _collect_branch_context,
    collect_review_context,
)
from lintro.ai.review.pr_head import remove_pr_head
from lintro.cli_utils.commands.review import review_command
from tests.unit.ai.review import pr_diff_fixtures
from tests.unit.ai.review import test_review_command_json_error as cli_helpers
from tests.unit.ai.review.test_pr_head_2733 import _git

pr_remote = pr_diff_fixtures.pr_remote
github = pr_diff_fixtures.github
patched_review = cli_helpers.patched_review


@pytest.mark.parametrize("depth", [1, 3])
@pytest.mark.parametrize("outcome", ["ancestor", "non-ancestor", "cap"])
def test_shallow_pr_head_ancestry(
    pr_remote: dict[str, Any],
    github: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    patched_review: None,
    depth: int,
    outcome: str,
) -> None:
    """Real truncated head histories deepen, reject unrelated bases, or hit the cap."""
    work = pr_remote["work"]
    for number in range(6):
        _git(work, "commit", "--allow-empty", "-m", f"head history {number}")
    head = _git(work, "rev-parse", "HEAD")
    _git(work, "push", "origin", "HEAD:refs/pull/1/head")
    github["head"] = head
    expected = _collect_branch_context(base=pr_remote["base"])
    clone = tmp_path / "pr-checkout"
    _git(tmp_path, "init", str(clone))
    _git(clone, "remote", "add", "origin", pr_remote["origin"].as_uri())
    _git(clone, "fetch", "--depth", str(depth), "origin", "refs/pull/1/head")
    _git(clone, "checkout", "--detach", "FETCH_HEAD")
    monkeypatch.chdir(clone)
    if outcome == "non-ancestor":
        github["compare"] = {"merge_base_commit": {"sha": pr_remote["base"]}}
    elif outcome == "cap":
        monkeypatch.setattr(
            "lintro.ai.review.context.pr_diff_fallback._HEAD_DEEPEN_STEPS",
            (1,),
        )
    if outcome != "ancestor":
        monkeypatch.setattr(
            preparation,
            "collect_review_context",
            collect_review_context,
        )
        result = CliRunner().invoke(
            review_command,
            ["--pr", "1", "--repo", "base/project", "--output", "json"],
        )
        assert_that(result.exit_code).is_equal_to(2)
        assert_that(result.output).contains("GitHub refused", "for size")
        if outcome == "cap":
            assert_that(result.output).contains(
                "could not verify within the shallow history available",
                "fetch-depth: 0",
            ).does_not_contain("not an ancestor")
        else:
            assert_that(result.output).contains("not an ancestor of the head")
            assert_that(result.output).does_not_contain("could not verify")
        return
    context = collect_review_context(pr_number=1, repo="base/project")
    try:
        assert_that(context.unified_diff).is_equal_to(expected.unified_diff)
        assert_that(context.changed_files).is_equal_to(expected.changed_files)
        assert_that(context.base_ref).is_equal_to(pr_remote["merge_base"])
        assert_that(context.diff_source).is_equal_to("local-git")
    finally:
        remove_pr_head(context.head_worktree)
