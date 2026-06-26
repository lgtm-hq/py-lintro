"""Tests for review diff context collection."""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.review.context import (
    collect_review_context,
    parse_changed_files,
    resolve_default_base_branch,
    split_unified_diff_by_file,
    validate_review_context_diff,
)
from lintro.ai.review.enums.review_context_error_code import ReviewContextErrorCode
from lintro.ai.review.exceptions import ReviewContextError
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.review_context import ReviewContext
from tests.unit.ai.review.conftest import SubprocessMock


def test_split_unified_diff_by_file_returns_sections(sample_unified_diff: str) -> None:
    """Unified diff splitting returns one section per changed file."""
    sections = split_unified_diff_by_file(unified_diff=sample_unified_diff)
    assert_that(sections).contains_key("scripts/ci/run.sh")
    assert_that(sections["scripts/ci/run.sh"]).contains('echo "running"')


@patch("lintro.ai.review.context.subprocess.run")
@patch("lintro.ai.review.context.shutil.which", return_value="/usr/bin/git")
def test_collect_branch_context_uses_merge_base(
    _mock_which: MagicMock,
    mock_run: MagicMock,
) -> None:
    """Branch review mode uses merge-base...HEAD diff commands."""
    dispatcher = SubprocessMock()
    dispatcher.queue(["git", "rev-parse", "--git-dir"], stdout=".git\n")
    dispatcher.queue(["git", "rev-parse", "--show-toplevel"], stdout="/repo/root\n")
    dispatcher.queue(["git", "merge-base", "main", "HEAD"], stdout="base123\n")
    dispatcher.queue(["git", "rev-parse", "HEAD"], stdout="head456\n")
    dispatcher.queue(
        ["git", "diff", "base123...head456"],
        stdout="diff --git a/a.py b/a.py\n",
    )
    dispatcher.queue(
        ["git", "diff", "--name-status", "base123...head456"],
        stdout="M\ta.py\n",
    )
    dispatcher.queue(
        ["git", "diff", "--numstat", "base123...head456"],
        stdout="1\t0\ta.py\n",
    )
    mock_run.side_effect = dispatcher

    context = collect_review_context(base="main")

    assert_that(context.base_ref).is_equal_to("base123")
    assert_that(context.head_ref).is_equal_to("head456")
    assert_that(context.repo_root).is_equal_to("/repo/root")
    assert_that(context.changed_files).is_length(1)
    assert_that(context.changed_files[0]).is_equal_to(
        ChangedFile(path="a.py", status="modified", additions=1, deletions=0),
    )
    diff_calls = [
        call.args[0]
        for call in mock_run.call_args_list
        if call.args[0][:2] == ["git", "diff"]
    ]
    assert_that(diff_calls).contains(["git", "diff", "base123...head456"])


@patch("lintro.ai.review.context.subprocess.run")
@patch("lintro.ai.review.context.shutil.which", return_value="/usr/bin/git")
def test_collect_uncommitted_context_merges_staged_and_unstaged(
    _mock_which: MagicMock,
    mock_run: MagicMock,
) -> None:
    """Uncommitted mode uses a single git diff HEAD for index and working tree."""
    dispatcher = SubprocessMock()
    dispatcher.queue(["git", "rev-parse", "--git-dir"], stdout=".git\n")
    dispatcher.queue(["git", "rev-parse", "--show-toplevel"], stdout="/repo/root\n")
    dispatcher.queue(["git", "ls-files", "--others", "--exclude-standard"], stdout="")
    dispatcher.queue(["git", "rev-parse", "HEAD"], stdout="head456\n")
    dispatcher.queue(
        ["git", "diff", "head456"],
        stdout=(
            "diff --git a/unstaged.py b/unstaged.py\n"
            "diff --git a/staged.py b/staged.py\n"
        ),
    )
    dispatcher.queue(
        ["git", "diff", "head456", "--name-status"],
        stdout="M\tunstaged.py\nA\tstaged.py\n",
    )
    dispatcher.queue(
        ["git", "diff", "head456", "--numstat"],
        stdout="1\t0\tunstaged.py\n2\t0\tstaged.py\n",
    )
    mock_run.side_effect = dispatcher

    context = collect_review_context(uncommitted=True)

    assert_that(context.base_ref).is_equal_to("WORKTREE")
    assert_that(context.repo_root).is_equal_to("/repo/root")
    assert_that(context.unified_diff).contains("unstaged.py")
    assert_that({file.path for file in context.changed_files}).is_equal_to(
        {"unstaged.py", "staged.py"},
    )
    diff_calls = [
        call.args[0]
        for call in mock_run.call_args_list
        if call.args[0][:2] == ["git", "diff"]
    ]
    assert_that(diff_calls).contains(["git", "diff", "head456"])


@patch("lintro.ai.review.context.subprocess.run")
@patch(
    "lintro.ai.review.context.shutil.which",
    side_effect=lambda cmd: "/usr/bin/gh" if cmd == "gh" else "/usr/bin/git",
)
def test_collect_pr_context_uses_gh(
    _mock_which: MagicMock,
    mock_run: MagicMock,
) -> None:
    """PR mode fetches diff and metadata via gh."""
    mock_run.side_effect = [
        _completed(
            stdout=(
                '{"title":"Fix bug","body":"Details","number":42,'
                '"baseRefOid":"abc123","headRefOid":"deadbeef",'
                '"repository":{"nameWithOwner":"lgtm-hq/py-lintro"}}'
            ),
        ),
        _completed(stdout="diff --git a/a.py b/a.py\n"),
    ]

    context = collect_review_context(pr_number=42, repo="lgtm-hq/py-lintro")

    assert_that(context.pr_metadata).is_not_none()
    metadata = context.pr_metadata
    assert metadata is not None
    assert_that(metadata.title).is_equal_to("Fix bug")
    assert_that(metadata.number).is_equal_to(42)
    assert_that(context.base_ref).is_equal_to("abc123")
    assert_that(context.head_ref).is_equal_to("deadbeef")
    assert_that(context.repo_root).is_equal_to("")


@patch("lintro.ai.review.context.subprocess.run")
@patch(
    "lintro.ai.review.context.shutil.which",
    side_effect=lambda cmd: "/usr/bin/gh" if cmd == "gh" else None,
)
def test_collect_pr_context_works_without_local_git_repo(
    _mock_which: MagicMock,
    mock_run: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR mode does not require a local git checkout."""
    monkeypatch.chdir(tmp_path)
    mock_run.side_effect = [
        _completed(
            stdout=(
                '{"title":"Fix bug","body":"Details","number":42,'
                '"baseRefOid":"abc123","headRefOid":"deadbeef",'
                '"repository":{"nameWithOwner":"lgtm-hq/py-lintro"}}'
            ),
        ),
        _completed(stdout="diff --git a/a.py b/a.py\n"),
    ]

    context = collect_review_context(pr_number=42, repo="lgtm-hq/py-lintro")

    assert_that(context.pr_metadata).is_not_none()
    assert_that(context.unified_diff).contains("a.py")


@patch("lintro.ai.review.context.subprocess.run")
@patch(
    "lintro.ai.review.context.shutil.which",
    side_effect=lambda cmd: "/usr/bin/gh" if cmd == "gh" else "/usr/bin/git",
)
def test_collect_pr_context_raises_when_view_fails(
    _mock_which: MagicMock,
    mock_run: MagicMock,
) -> None:
    """PR mode raises when gh pr view metadata fetch fails."""
    mock_run.return_value = CompletedProcess(
        args=[],
        returncode=1,
        stdout="",
        stderr="view failed",
    )

    with pytest.raises(
        ReviewContextError,
        match="Failed to load pull request metadata",
    ):
        collect_review_context(pr_number=42)


@patch("lintro.ai.review.context.subprocess.run")
@patch(
    "lintro.ai.review.context.shutil.which",
    side_effect=lambda cmd: "/usr/bin/gh" if cmd == "gh" else "/usr/bin/git",
)
def test_collect_pr_context_raises_on_malformed_json(
    _mock_which: MagicMock,
    mock_run: MagicMock,
) -> None:
    """Malformed gh JSON surfaces as a review context error."""
    mock_run.return_value = _completed(stdout="{not-json")

    with pytest.raises(ReviewContextError) as exc_info:
        collect_review_context(pr_number=42)
    assert_that(exc_info.value.code).is_equal_to(ReviewContextErrorCode.GH_JSON_INVALID)


@patch("lintro.ai.review.context.subprocess.run")
@patch("lintro.ai.review.context.shutil.which", return_value="/usr/bin/git")
def test_collect_review_context_filters_paths(
    _mock_which: MagicMock,
    mock_run: MagicMock,
) -> None:
    """Path filters limit changed files and diff hunks."""
    mock_run.side_effect = [
        _completed(stdout=".git\n"),
        _completed(stdout="/repo/root\n"),
        _completed(stdout="base123\n"),
        _completed(stdout="head456\n"),
        _completed(
            stdout=(
                "diff --git a/a.py b/a.py\n"
                "+++ b/a.py\n"
                "+a\n"
                "diff --git a/pkg/b.py b/pkg/b.py\n"
                "+++ b/pkg/b.py\n"
                "+b\n"
            ),
        ),
        _completed(stdout="M\ta.py\nM\tpkg/b.py\n"),
        _completed(stdout="1\t0\ta.py\n1\t0\tpkg/b.py\n"),
    ]

    context = collect_review_context(base="main", paths=["pkg/"])

    assert_that(context.changed_files).extracting("path").is_equal_to(["pkg/b.py"])
    assert_that(context.unified_diff).contains("pkg/b.py")
    assert_that(context.unified_diff).does_not_contain("a.py")


@patch("lintro.ai.review.context.shutil.which", return_value=None)
def test_collect_review_context_requires_git(_mock_which: MagicMock) -> None:
    """Missing git binary raises a clear review context error."""
    with pytest.raises(ReviewContextError) as exc_info:
        collect_review_context()
    assert_that(exc_info.value.code).is_equal_to(ReviewContextErrorCode.GIT_UNAVAILABLE)


@patch("lintro.ai.review.context.subprocess.run")
@patch("lintro.ai.review.context.shutil.which", return_value="/usr/bin/git")
def test_collect_review_context_raises_on_empty_diff(
    _mock_which: MagicMock,
    mock_run: MagicMock,
) -> None:
    """Empty diffs raise a review context error."""
    mock_run.side_effect = [
        _completed(stdout=".git\n"),
        _completed(stdout="/repo/root\n"),
        _completed(stdout="base123\n"),
        _completed(stdout="head456\n"),
        _completed(stdout=""),
        _completed(stdout=""),
        _completed(stdout=""),
    ]

    with pytest.raises(ReviewContextError) as exc_info:
        collect_review_context(base="main")
    assert_that(exc_info.value.code).is_equal_to(ReviewContextErrorCode.NO_CHANGES)


@patch("lintro.ai.review.context.subprocess.run")
@patch("lintro.ai.review.context.shutil.which", return_value="/usr/bin/git")
def test_collect_review_context_raises_on_metadata_diff_desync(
    _mock_which: MagicMock,
    mock_run: MagicMock,
) -> None:
    """Non-empty changed_files with an empty unified diff fail fast."""
    mock_run.side_effect = [
        _completed(stdout=".git\n"),
        _completed(stdout="/repo/root\n"),
        _completed(stdout="base123\n"),
        _completed(stdout="head456\n"),
        _completed(stdout=""),
        _completed(stdout="M\ta.py\n"),
        _completed(stdout="1\t0\ta.py\n"),
    ]

    with pytest.raises(ReviewContextError) as exc_info:
        collect_review_context(base="main")
    assert_that(exc_info.value.code).is_equal_to(ReviewContextErrorCode.DIFF_DESYNC)


@patch("lintro.ai.review.context.subprocess.run")
@patch("lintro.ai.review.context.shutil.which", return_value="/usr/bin/git")
def test_resolve_default_base_branch_falls_back_to_verified_branch(
    _mock_which: MagicMock,
    mock_run: MagicMock,
) -> None:
    """Default branch detection verifies a local branch when origin/HEAD is missing."""
    mock_run.side_effect = [
        _completed(returncode=1),
        _completed(stdout="deadbeef\n"),
    ]

    assert_that(resolve_default_base_branch()).is_equal_to("main")


@patch("lintro.ai.review.context.subprocess.run")
@patch("lintro.ai.review.context.shutil.which", return_value="/usr/bin/git")
def test_resolve_default_base_branch_raises_when_undetectable(
    _mock_which: MagicMock,
    mock_run: MagicMock,
) -> None:
    """Default branch detection raises when no candidate branch exists."""
    mock_run.side_effect = [
        _completed(returncode=1),
        _completed(returncode=1),
        _completed(returncode=1),
        _completed(returncode=1),
        _completed(returncode=1),
        _completed(returncode=1),
        _completed(returncode=1),
    ]

    with pytest.raises(ReviewContextError) as exc_info:
        resolve_default_base_branch()
    assert_that(exc_info.value.code).is_equal_to(
        ReviewContextErrorCode.DEFAULT_BRANCH_UNKNOWN,
    )


def test_validate_review_context_diff_rejects_unparseable_diff_without_files() -> None:
    """Non-empty diffs that fail to parse raise before review proceeds."""
    context = ReviewContext(
        base_ref="abc",
        head_ref="def",
        changed_files=[],
        unified_diff="not a valid unified diff header\n",
        pr_metadata=None,
    )

    with pytest.raises(ReviewContextError) as exc_info:
        validate_review_context_diff(context=context)
    assert_that(exc_info.value.code).is_equal_to(
        ReviewContextErrorCode.NO_PARSEABLE_DIFF,
    )


@pytest.mark.parametrize(
    ("name_status", "numstat", "expected_path", "expected_status"),
    [
        pytest.param(
            "R100\told_name.py\tnew_name.py\n",
            "1\t0\tnew_name.py\n",
            "new_name.py",
            "renamed",
            id="rename_status",
        ),
        pytest.param(
            "A\tadded.py\n",
            "3\t0\tadded.py\n",
            "added.py",
            "added",
            id="added_status",
        ),
        pytest.param(
            "C100\told.py\tnew.py\n",
            "1\t0\tnew.py\n",
            "new.py",
            "copied",
            id="copied_status",
        ),
        pytest.param(
            "T\tfile.py\n",
            "1\t1\tfile.py\n",
            "file.py",
            "type-changed",
            id="type_changed_status",
        ),
    ],
)
def test_parse_changed_files_normalizes_git_status(
    *,
    name_status: str,
    numstat: str,
    expected_path: str,
    expected_status: str,
) -> None:
    """Git name-status codes are parsed into normalized changed-file entries."""
    changed_files = parse_changed_files(name_status=name_status, numstat=numstat)

    assert_that(changed_files).is_length(1)
    assert_that(changed_files[0].path).is_equal_to(expected_path)
    assert_that(changed_files[0].status).is_equal_to(expected_status)


def _completed(
    *,
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> CompletedProcess[str]:
    """Build a minimal subprocess result stand-in."""
    return CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )
