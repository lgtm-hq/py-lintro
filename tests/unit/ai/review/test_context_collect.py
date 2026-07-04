"""Tests for review context collection with mocked subprocesses."""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.review.context import (
    collect_review_context,
    resolve_default_base_branch,
)
from lintro.ai.review.enums.changed_file_status import ChangedFileStatus
from lintro.ai.review.enums.review_context_error_code import ReviewContextErrorCode
from lintro.ai.review.exceptions import ReviewContextError
from lintro.ai.review.models.changed_file import ChangedFile
from tests.unit.ai.review.conftest import (
    SubprocessMock,
    queue_diff_snapshot,
)


def _which_for_review_tools(cmd: str) -> str | None:
    """Return stable executable paths for review context subprocess tests."""
    return {
        "bash": "/usr/bin/bash",
        "gh": "/usr/bin/gh",
        "git": "/usr/bin/git",
    }.get(cmd)


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


@patch("lintro.ai.review.context.git_ops.subprocess.run")
@patch(
    "lintro.ai.review.context.git_ops.shutil.which",
    side_effect=_which_for_review_tools,
)
def test_collect_review_context_filters_paths_preserves_diff_preamble(
    _mock_which: MagicMock,
    mock_run: MagicMock,
) -> None:
    """Path filters retain any bytes that precede the first diff header."""
    dispatcher = SubprocessMock()
    dispatcher.queue(["git", "rev-parse", "--git-dir"], stdout=".git\n")
    dispatcher.queue(["git", "merge-base", "main", "HEAD"], stdout="base123\n")
    dispatcher.queue(["git", "rev-parse", "HEAD"], stdout="head456\n")
    queue_diff_snapshot(
        dispatcher,
        diff_ref="base123...head456",
        unified=(
            "==> PR status\n\n"
            "diff --git a/a.py b/a.py\n"
            "+++ b/a.py\n"
            "+a\n"
            "diff --git a/pkg/b.py b/pkg/b.py\n"
            "+++ b/pkg/b.py\n"
            "+b\n"
        ),
        name_status="M\0a.py\0M\0pkg/b.py\0",
        numstat="1\t0\ta.py\01\t0\tpkg/b.py\0",
    )
    mock_run.side_effect = dispatcher

    context = collect_review_context(base="main", paths=["pkg/"])

    assert_that(context.unified_diff).starts_with("==> PR status\n\n")
    assert_that(context.unified_diff).contains("pkg/b.py")
    assert_that(context.unified_diff).does_not_contain("a.py")


@patch("lintro.ai.review.context.git_ops.subprocess.run")
@patch(
    "lintro.ai.review.context.git_ops.shutil.which",
    side_effect=_which_for_review_tools,
)
def test_collect_review_context_path_filter_omits_preamble_when_no_matches(
    _mock_which: MagicMock,
    mock_run: MagicMock,
) -> None:
    """An all-excluded path filter returns an empty diff without stray preamble."""
    dispatcher = SubprocessMock()
    dispatcher.queue(["git", "rev-parse", "--git-dir"], stdout=".git\n")
    dispatcher.queue(["git", "merge-base", "main", "HEAD"], stdout="base123\n")
    dispatcher.queue(["git", "rev-parse", "HEAD"], stdout="head456\n")
    queue_diff_snapshot(
        dispatcher,
        diff_ref="base123...head456",
        unified=(
            "==> PR status\n\n" "diff --git a/a.py b/a.py\n" "+++ b/a.py\n" "+a\n"
        ),
        name_status="M\0a.py\0",
        numstat="1\t0\ta.py\0",
    )
    mock_run.side_effect = dispatcher

    with pytest.raises(ReviewContextError) as exc_info:
        collect_review_context(base="main", paths=["missing/"])
    assert_that(exc_info.value.code).is_equal_to(ReviewContextErrorCode.NO_CHANGES)


@patch("lintro.ai.review.context.git_ops.subprocess.run")
@patch(
    "lintro.ai.review.context.git_ops.shutil.which",
    side_effect=_which_for_review_tools,
)
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
    queue_diff_snapshot(
        dispatcher,
        diff_ref="base123...head456",
        unified="diff --git a/a.py b/a.py\n",
        name_status="M\0a.py\0",
        numstat="1\t0\ta.py\0",
    )
    mock_run.side_effect = dispatcher

    context = collect_review_context(base="main")

    assert_that(context.base_ref).is_equal_to("base123")
    assert_that(context.head_ref).is_equal_to("head456")
    assert_that(context.repo_root).is_equal_to("/repo/root")
    assert_that(context.changed_files).is_length(1)
    assert_that(context.changed_files[0]).is_equal_to(
        ChangedFile(
            path="a.py",
            status=ChangedFileStatus.MODIFIED,
            additions=1,
            deletions=0,
        ),
    )
    bash_calls = [
        call.args[0]
        for call in mock_run.call_args_list
        if [Path(call.args[0][0]).name, *call.args[0][1:2]] == ["bash", "-c"]
    ]
    assert_that(bash_calls).is_length(1)
    assert_that(bash_calls[0][2]).contains("base123...head456")


@patch("lintro.ai.review.context.git_ops.subprocess.run")
@patch(
    "lintro.ai.review.context.git_ops.shutil.which",
    side_effect=_which_for_review_tools,
)
def test_collect_uncommitted_context_merges_staged_and_unstaged(
    _mock_which: MagicMock,
    mock_run: MagicMock,
) -> None:
    """Uncommitted mode uses a single git diff HEAD for index and working tree."""
    dispatcher = SubprocessMock()
    dispatcher.queue(["git", "rev-parse", "--git-dir"], stdout=".git\n")
    dispatcher.queue(["git", "ls-files", "--others", "--exclude-standard"], stdout="")
    dispatcher.queue(["git", "rev-parse", "HEAD"], stdout="head456\n")
    queue_diff_snapshot(
        dispatcher,
        diff_ref="head456",
        unified=(
            "diff --git a/unstaged.py b/unstaged.py\n"
            "diff --git a/staged.py b/staged.py\n"
        ),
        name_status="M\0unstaged.py\0A\0staged.py\0",
        numstat="1\t0\tunstaged.py\02\t0\tstaged.py\0",
    )
    mock_run.side_effect = dispatcher

    context = collect_review_context(uncommitted=True)

    assert_that(context.base_ref).is_equal_to("head456")
    assert_that(context.head_ref).is_equal_to("WORKTREE")
    assert_that(context.unified_diff).contains("unstaged.py")
    assert_that({file.path for file in context.changed_files}).is_equal_to(
        {"unstaged.py", "staged.py"},
    )
    bash_calls = [
        call.args[0]
        for call in mock_run.call_args_list
        if [Path(call.args[0][0]).name, *call.args[0][1:2]] == ["bash", "-c"]
    ]
    assert_that(bash_calls).is_length(1)
    assert_that(bash_calls[0][2]).contains("head456")


@patch("lintro.ai.review.context.git_ops.subprocess.run")
@patch(
    "lintro.ai.review.context.git_ops.shutil.which",
    side_effect=_which_for_review_tools,
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
                '"baseRepository":{"nameWithOwner":"fork-owner/fork-repo"},'
                '"headRepository":{"nameWithOwner":"lgtm-hq/py-lintro"}}'
            ),
        ),
        _completed(
            stdout=(
                "diff --git a/a.py b/a.py\n"
                "--- a/a.py\n"
                "+++ b/a.py\n"
                "@@ -1 +1 @@\n"
                "-old\n"
                "+new\n"
            ),
        ),
    ]

    context = collect_review_context(pr_number=42, repo="lgtm-hq/py-lintro")

    for call in mock_run.call_args_list:
        argv = call.args[0]
        assert_that(Path(argv[0]).name).is_equal_to("gh")
        assert_that(argv).contains("--repo", "lgtm-hq/py-lintro")

    assert_that(context.pr_metadata).is_not_none()
    metadata = context.pr_metadata
    assert metadata is not None
    assert_that(metadata.title).is_equal_to("Fix bug")
    assert_that(metadata.number).is_equal_to(42)
    assert_that(metadata.repo).is_equal_to("lgtm-hq/py-lintro")
    assert_that(context.base_ref).is_equal_to("abc123")
    assert_that(context.head_ref).is_equal_to("deadbeef")
    assert_that(context.changed_files).extracting("path").contains("a.py")


@patch("lintro.ai.review.context.git_ops.subprocess.run")
@patch(
    "lintro.ai.review.context.git_ops.shutil.which",
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
                '"headRepository":{"nameWithOwner":"lgtm-hq/py-lintro"}}'
            ),
        ),
        _completed(
            stdout=(
                "diff --git a/a.py b/a.py\n"
                "--- a/a.py\n"
                "+++ b/a.py\n"
                "@@ -1 +1 @@\n"
                "-old\n"
                "+new\n"
            ),
        ),
    ]

    context = collect_review_context(pr_number=42, repo="lgtm-hq/py-lintro")

    for call in mock_run.call_args_list:
        argv = call.args[0]
        assert_that(Path(argv[0]).name).is_equal_to("gh")
        assert_that(argv).contains("--repo", "lgtm-hq/py-lintro")

    assert_that(context.pr_metadata).is_not_none()
    assert_that(context.unified_diff).contains("a.py")
    assert_that(context.changed_files).extracting("path").contains("a.py")


@patch("lintro.ai.review.context.git_ops.subprocess.run")
@patch(
    "lintro.ai.review.context.git_ops.shutil.which",
    side_effect=_which_for_review_tools,
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


@patch("lintro.ai.review.context.git_ops.subprocess.run")
@patch(
    "lintro.ai.review.context.git_ops.shutil.which",
    side_effect=_which_for_review_tools,
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


def test_collect_review_context_rejects_conflicting_pr_and_uncommitted_modes() -> None:
    """PR mode and uncommitted mode cannot be combined."""
    with pytest.raises(ReviewContextError) as exc_info:
        collect_review_context(pr_number=42, uncommitted=True)
    assert_that(exc_info.value.code).is_equal_to(
        ReviewContextErrorCode.INVALID_REVIEW_MODE,
    )


def test_collect_review_context_rejects_conflicting_pr_and_base() -> None:
    """PR mode ignores an explicit base branch — callers must not combine them."""
    with pytest.raises(ReviewContextError) as exc_info:
        collect_review_context(pr_number=42, base="main")
    assert_that(exc_info.value.code).is_equal_to(
        ReviewContextErrorCode.INVALID_REVIEW_MODE,
    )


def test_collect_review_context_rejects_uncommitted_with_explicit_base() -> None:
    """Uncommitted mode cannot be combined with an explicit base branch."""
    with pytest.raises(ReviewContextError) as exc_info:
        collect_review_context(uncommitted=True, base="main")
    assert_that(exc_info.value.code).is_equal_to(
        ReviewContextErrorCode.INVALID_REVIEW_MODE,
    )


def test_collect_review_context_rejects_repo_without_pr_number() -> None:
    """Repository override is valid only for PR mode."""
    with pytest.raises(ReviewContextError) as exc_info:
        collect_review_context(repo="owner/repo")
    assert_that(exc_info.value.code).is_equal_to(
        ReviewContextErrorCode.INVALID_REVIEW_MODE,
    )


@patch("lintro.ai.review.context.git_ops.subprocess.run")
@patch(
    "lintro.ai.review.context.git_ops.shutil.which",
    side_effect=lambda cmd: None if cmd == "bash" else "/usr/bin/git",
)
def test_collect_review_context_requires_bash_for_git_modes(
    _mock_which: MagicMock,
    mock_run: MagicMock,
) -> None:
    """Branch and uncommitted collection require bash for combined diff output."""
    with pytest.raises(ReviewContextError) as exc_info:
        collect_review_context(base="main")
    assert_that(exc_info.value.code).is_equal_to(
        ReviewContextErrorCode.BASH_UNAVAILABLE,
    )
    mock_run.assert_not_called()


@patch("lintro.ai.review.context.git_ops.subprocess.run")
@patch(
    "lintro.ai.review.context.git_ops.shutil.which",
    side_effect=_which_for_review_tools,
)
def test_collect_review_context_filters_paths(
    _mock_which: MagicMock,
    mock_run: MagicMock,
) -> None:
    """Path filters limit changed files and diff hunks."""
    dispatcher = SubprocessMock()
    dispatcher.queue(["git", "rev-parse", "--git-dir"], stdout=".git\n")
    dispatcher.queue(["git", "merge-base", "main", "HEAD"], stdout="base123\n")
    dispatcher.queue(["git", "rev-parse", "HEAD"], stdout="head456\n")
    queue_diff_snapshot(
        dispatcher,
        diff_ref="base123...head456",
        unified=(
            "diff --git a/a.py b/a.py\n"
            "+++ b/a.py\n"
            "+a\n"
            "diff --git a/pkg/b.py b/pkg/b.py\n"
            "+++ b/pkg/b.py\n"
            "+b\n"
        ),
        name_status="M\0a.py\0M\0pkg/b.py\0",
        numstat="1\t0\ta.py\01\t0\tpkg/b.py\0",
    )
    mock_run.side_effect = dispatcher

    context = collect_review_context(base="main", paths=["pkg/"])

    assert_that(context.changed_files).extracting("path").is_equal_to(["pkg/b.py"])
    assert_that(context.unified_diff).contains("pkg/b.py")
    assert_that(context.unified_diff).does_not_contain("a.py")


@patch(
    "lintro.ai.review.context.git_ops.shutil.which",
    side_effect=lambda cmd: "/usr/bin/bash" if cmd == "bash" else None,
)
def test_collect_review_context_requires_git(_mock_which: MagicMock) -> None:
    """Missing git binary raises a clear review context error."""
    with pytest.raises(ReviewContextError) as exc_info:
        collect_review_context()
    assert_that(exc_info.value.code).is_equal_to(ReviewContextErrorCode.GIT_UNAVAILABLE)


@patch("lintro.ai.review.context.git_ops.subprocess.run")
@patch(
    "lintro.ai.review.context.git_ops.shutil.which",
    side_effect=_which_for_review_tools,
)
def test_collect_review_context_filters_paths_with_dot_slash_prefix(
    _mock_which: MagicMock,
    mock_run: MagicMock,
) -> None:
    """Path filters accept ./pkg prefixes."""
    dispatcher = SubprocessMock()
    dispatcher.queue(["git", "rev-parse", "--git-dir"], stdout=".git\n")
    dispatcher.queue(["git", "merge-base", "main", "HEAD"], stdout="base123\n")
    dispatcher.queue(["git", "rev-parse", "HEAD"], stdout="head456\n")
    queue_diff_snapshot(
        dispatcher,
        diff_ref="base123...head456",
        unified="diff --git a/pkg/b.py b/pkg/b.py\n+++ b/pkg/b.py\n+b\n",
        name_status="M\0pkg/b.py\0",
        numstat="1\t0\tpkg/b.py\0",
    )
    mock_run.side_effect = dispatcher

    context = collect_review_context(base="main", paths=["./pkg"])

    assert_that(context.changed_files).extracting("path").is_equal_to(["pkg/b.py"])


@patch("lintro.ai.review.context.git_ops.subprocess.run")
@patch(
    "lintro.ai.review.context.git_ops.shutil.which",
    side_effect=_which_for_review_tools,
)
def test_collect_review_context_filters_paths_with_root_slash_prefix(
    _mock_which: MagicMock,
    mock_run: MagicMock,
) -> None:
    """Path filters accept /pkg root-relative prefixes."""
    dispatcher = SubprocessMock()
    dispatcher.queue(["git", "rev-parse", "--git-dir"], stdout=".git\n")
    dispatcher.queue(["git", "merge-base", "main", "HEAD"], stdout="base123\n")
    dispatcher.queue(["git", "rev-parse", "HEAD"], stdout="head456\n")
    queue_diff_snapshot(
        dispatcher,
        diff_ref="base123...head456",
        unified="diff --git a/pkg/b.py b/pkg/b.py\n+++ b/pkg/b.py\n+b\n",
        name_status="M\0pkg/b.py\0",
        numstat="1\t0\tpkg/b.py\0",
    )
    mock_run.side_effect = dispatcher

    context = collect_review_context(base="main", paths=["/pkg"])

    assert_that(context.changed_files).extracting("path").is_equal_to(["pkg/b.py"])


@patch("lintro.ai.review.context.git_ops.subprocess.run")
@patch(
    "lintro.ai.review.context.git_ops.shutil.which",
    side_effect=_which_for_review_tools,
)
def test_collect_review_context_raises_on_empty_diff(
    _mock_which: MagicMock,
    mock_run: MagicMock,
) -> None:
    """Empty diffs raise a review context error."""
    dispatcher = SubprocessMock()
    dispatcher.queue(["git", "rev-parse", "--git-dir"], stdout=".git\n")
    dispatcher.queue(["git", "merge-base", "main", "HEAD"], stdout="base123\n")
    dispatcher.queue(["git", "rev-parse", "HEAD"], stdout="head456\n")
    queue_diff_snapshot(
        dispatcher,
        diff_ref="base123...head456",
        unified="",
        name_status="",
        numstat="",
    )
    mock_run.side_effect = dispatcher

    with pytest.raises(ReviewContextError) as exc_info:
        collect_review_context(base="main")
    assert_that(exc_info.value.code).is_equal_to(ReviewContextErrorCode.NO_CHANGES)


@patch("lintro.ai.review.context.git_ops.subprocess.run")
@patch(
    "lintro.ai.review.context.git_ops.shutil.which",
    side_effect=_which_for_review_tools,
)
def test_collect_review_context_raises_on_metadata_diff_desync(
    _mock_which: MagicMock,
    mock_run: MagicMock,
) -> None:
    """Non-empty changed_files with an empty unified diff fail fast."""
    dispatcher = SubprocessMock()
    dispatcher.queue(["git", "rev-parse", "--git-dir"], stdout=".git\n")
    dispatcher.queue(["git", "merge-base", "main", "HEAD"], stdout="base123\n")
    dispatcher.queue(["git", "rev-parse", "HEAD"], stdout="head456\n")
    queue_diff_snapshot(
        dispatcher,
        diff_ref="base123...head456",
        unified="",
        name_status="M\0a.py\0",
        numstat="1\t0\ta.py\0",
    )
    mock_run.side_effect = dispatcher

    with pytest.raises(ReviewContextError) as exc_info:
        collect_review_context(base="main")
    assert_that(exc_info.value.code).is_equal_to(ReviewContextErrorCode.DIFF_DESYNC)


@patch("lintro.ai.review.context.git_ops.subprocess.run")
@patch(
    "lintro.ai.review.context.git_ops.shutil.which",
    side_effect=_which_for_review_tools,
)
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


@patch("lintro.ai.review.context.git_ops.subprocess.run")
@patch(
    "lintro.ai.review.context.git_ops.shutil.which",
    side_effect=_which_for_review_tools,
)
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


@patch("lintro.ai.review.context.git_ops.subprocess.run")
@patch(
    "lintro.ai.review.context.git_ops.shutil.which",
    side_effect=_which_for_review_tools,
)
def test_collect_review_context_filters_renamed_files_by_previous_path(
    _mock_which: MagicMock,
    mock_run: MagicMock,
) -> None:
    """Path filters retain renames when the source path matches the prefix."""
    dispatcher = SubprocessMock()
    dispatcher.queue(["git", "rev-parse", "--git-dir"], stdout=".git\n")
    dispatcher.queue(["git", "merge-base", "main", "HEAD"], stdout="base123\n")
    dispatcher.queue(["git", "rev-parse", "HEAD"], stdout="head456\n")
    queue_diff_snapshot(
        dispatcher,
        diff_ref="base123...head456",
        unified=(
            "diff --git a/pkg/old.py b/lib/new.py\n"
            "rename from pkg/old.py\n"
            "rename to lib/new.py\n"
            "--- a/pkg/old.py\n"
            "+++ b/lib/new.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        ),
        name_status="R100\0pkg/old.py\0lib/new.py\0",
        numstat="1\t1\t\0pkg/old.py\0lib/new.py\0",
    )
    mock_run.side_effect = dispatcher

    context = collect_review_context(base="main", paths=["pkg/"])

    assert_that(context.changed_files).is_length(1)
    assert_that(context.unified_diff).contains("lib/new.py")


@patch("lintro.ai.review.context.git_ops.subprocess.run")
@patch(
    "lintro.ai.review.context.git_ops.shutil.which",
    side_effect=_which_for_review_tools,
)
def test_collect_pr_context_accepts_repo_override_when_head_repository_null(
    _mock_which: MagicMock,
    mock_run: MagicMock,
) -> None:
    """PR mode uses explicit repo override when gh omits headRepository."""
    mock_run.side_effect = [
        _completed(
            stdout=(
                '{"title":"Fix bug","body":"Details","number":42,'
                '"baseRefOid":"abc123","headRefOid":"deadbeef",'
                '"baseRepository":{"nameWithOwner":"fork-owner/fork-repo"},'
                '"headRepository":null}'
            ),
        ),
        _completed(
            stdout=(
                "diff --git a/a.py b/a.py\n"
                "--- a/a.py\n"
                "+++ b/a.py\n"
                "@@ -1 +1 @@\n"
                "-old\n"
                "+new\n"
            ),
        ),
    ]

    context = collect_review_context(pr_number=42, repo="lgtm-hq/py-lintro")

    assert_that(context.pr_metadata).is_not_none()
    metadata = context.pr_metadata
    assert metadata is not None
    assert_that(metadata.repo).is_equal_to("lgtm-hq/py-lintro")


@patch("lintro.ai.review.context.git_ops.subprocess.run")
@patch(
    "lintro.ai.review.context.git_ops.shutil.which",
    side_effect=_which_for_review_tools,
)
def test_collect_pr_context_fetches_workflow_post_image_via_gh(
    _mock_which: MagicMock,
    mock_run: MagicMock,
) -> None:
    """PR mode falls back to gh when local git cannot show workflow head content."""
    mock_run.side_effect = [
        _completed(
            stdout=(
                '{"title":"CI","body":"","number":7,'
                '"baseRefOid":"abc123","headRefOid":"deadbeef",'
                '"headRepository":{"nameWithOwner":"lgtm-hq/py-lintro"}}'
            ),
        ),
        _completed(
            stdout=(
                "diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml\n"
                "--- a/.github/workflows/ci.yml\n"
                "+++ b/.github/workflows/ci.yml\n"
                "@@ -1 +1,2 @@\n"
                " name: CI\n"
                "+env:\n"
                " run: scripts/deploy.sh\n"
            ),
        ),
        _completed(returncode=1, stdout="", stderr="bad object"),
        _completed(stdout="name: CI\nenv:\n  CI: true\nrun: scripts/deploy.sh\n"),
    ]

    context = collect_review_context(pr_number=7, repo="lgtm-hq/py-lintro")

    gh_api_calls = [
        call.args[0]
        for call in mock_run.call_args_list
        if Path(call.args[0][0]).name == "gh" and "api" in call.args[0]
    ]
    assert_that(gh_api_calls).is_length(1)
    assert_that(gh_api_calls[0]).contains(
        "repos/lgtm-hq/py-lintro/contents/.github/workflows/ci.yml?ref=deadbeef",
    )
    assert_that(gh_api_calls[0]).does_not_contain("-f")
    assert_that(context.post_image_files[".github/workflows/ci.yml"]).contains(
        "run: scripts/deploy.sh",
    )


@patch("lintro.ai.review.context.git_ops.subprocess.run")
@patch(
    "lintro.ai.review.context.git_ops.shutil.which",
    side_effect=_which_for_review_tools,
)
def test_collect_pr_context_uses_head_repository_for_workflow_fetch(
    _mock_which: MagicMock,
    mock_run: MagicMock,
) -> None:
    """Fork PR workflow post-image fetch uses the head repository, not the base."""
    mock_run.side_effect = [
        _completed(
            stdout=(
                '{"title":"Fork PR","body":"","number":9,'
                '"baseRefOid":"abc123","headRefOid":"forksha",'
                '"baseRepository":{"nameWithOwner":"lgtm-hq/py-lintro"},'
                '"headRepository":{"nameWithOwner":"fork-owner/py-lintro"}}'
            ),
        ),
        _completed(
            stdout=(
                "diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml\n"
                "--- a/.github/workflows/ci.yml\n"
                "+++ b/.github/workflows/ci.yml\n"
                "@@ -1 +1,2 @@\n"
                " name: CI\n"
                "+env:\n"
                " run: scripts/deploy.sh\n"
            ),
        ),
        _completed(returncode=1, stdout="", stderr="bad object"),
        _completed(stdout="name: CI\nenv:\n  CI: true\nrun: scripts/deploy.sh\n"),
    ]

    context = collect_review_context(pr_number=9)

    metadata = context.pr_metadata
    assert metadata is not None
    assert_that(metadata.repo).is_equal_to("lgtm-hq/py-lintro")
    assert_that(metadata.head_repo).is_equal_to("fork-owner/py-lintro")
    gh_api_calls = [
        call.args[0]
        for call in mock_run.call_args_list
        if Path(call.args[0][0]).name == "gh" and "api" in call.args[0]
    ]
    assert_that(gh_api_calls[0]).contains(
        "repos/fork-owner/py-lintro/contents/.github/workflows/ci.yml?ref=forksha",
    )


@patch("lintro.ai.review.context.collection._run_git")
@patch("lintro.ai.review.context.git_ops.subprocess.run")
@patch(
    "lintro.ai.review.context.git_ops.shutil.which",
    side_effect=_which_for_review_tools,
)
def test_collect_pr_context_fetches_workflow_via_gh_when_git_show_raises(
    _mock_which: MagicMock,
    mock_run: MagicMock,
    mock_run_git: MagicMock,
) -> None:
    """PR mode still tries gh when local git show raises ReviewContextError."""
    mock_run_git.side_effect = ReviewContextError(
        "git unavailable",
        code=ReviewContextErrorCode.GIT_UNAVAILABLE,
    )
    mock_run.side_effect = [
        _completed(
            stdout=(
                '{"title":"CI","body":"","number":8,'
                '"baseRefOid":"abc123","headRefOid":"deadbeef",'
                '"headRepository":{"nameWithOwner":"lgtm-hq/py-lintro"}}'
            ),
        ),
        _completed(
            stdout=(
                "diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml\n"
                "--- a/.github/workflows/ci.yml\n"
                "+++ b/.github/workflows/ci.yml\n"
                "@@ -1 +1,2 @@\n"
                " name: CI\n"
                "+env:\n"
                " run: scripts/deploy.sh\n"
            ),
        ),
        _completed(stdout="name: CI\nenv:\n  CI: true\nrun: scripts/deploy.sh\n"),
    ]

    context = collect_review_context(pr_number=8, repo="lgtm-hq/py-lintro")

    assert_that(mock_run_git.call_count).is_greater_than(0)
    assert_that(context.post_image_files[".github/workflows/ci.yml"]).contains(
        "run: scripts/deploy.sh",
    )


@patch("lintro.ai.review.context.collection._run_git")
def test_read_workflow_post_image_preserves_empty_file(
    mock_run_git: MagicMock,
) -> None:
    """An emptied workflow at head is preserved as empty, not treated as missing."""
    from lintro.ai.review.context.collection import _read_workflow_post_image

    mock_run_git.return_value = _completed(stdout="")

    content = _read_workflow_post_image(
        path=".github/workflows/ci.yml",
        head_ref="deadbeef",
    )

    assert_that(content).is_equal_to("")


@patch("lintro.ai.review.context.collection._run_gh")
@patch("lintro.ai.review.context.collection._run_git")
def test_read_workflow_post_image_preserves_empty_file_via_gh(
    mock_run_git: MagicMock,
    mock_run_gh: MagicMock,
) -> None:
    """Gh raw-content fallback preserves an emptied workflow file at head."""
    from lintro.ai.review.context.collection import _read_workflow_post_image

    mock_run_git.return_value = _completed(returncode=1, stdout="", stderr="bad object")
    mock_run_gh.return_value = _completed(stdout="")

    content = _read_workflow_post_image(
        path=".github/workflows/ci.yml",
        head_ref="deadbeef",
        repo="lgtm-hq/py-lintro",
    )

    assert_that(content).is_equal_to("")


def test_normalize_path_prefix_strips_mixed_root_prefixes() -> None:
    """Path filters normalize mixed / and ./ root-relative prefixes."""
    from lintro.ai.review.context.collection import _normalize_path_prefix

    assert_that(_normalize_path_prefix(path="/./pkg")).is_equal_to("pkg")


def test_normalize_path_prefix_preserves_edge_whitespace() -> None:
    """Path filters keep leading/trailing spaces so legal git paths still match."""
    from lintro.ai.review.context.collection import (
        _normalize_path_prefix,
        _path_matches_any_prefix,
    )

    spaced_prefix = _normalize_path_prefix(path=" foo/")
    assert_that(spaced_prefix).is_equal_to(" foo")
    assert_that(
        _path_matches_any_prefix(path=" foo", prefixes=[spaced_prefix]),
    ).is_true()
    assert_that(
        _path_matches_any_prefix(path="foo", prefixes=[spaced_prefix]),
    ).is_false()
