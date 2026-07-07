"""Integration tests for review context collection with real git."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

from lintro.ai.review.context import collect_review_context
from lintro.ai.review.pipeline import prepare_review_chunks


@dataclass(frozen=True)
class GitRepo:
    """Temporary git repository with resolved git executable."""

    path: Path
    git_bin: str

    def __truediv__(self, child: str) -> Path:
        """Return a child path inside the repository."""
        return self.path / child


def _run_git(
    git_repo: GitRepo,
    *args: str,
    **kwargs: Any,
) -> subprocess.CompletedProcess[bytes]:
    """Run git in the test repo with a single security suppression."""
    kwargs.setdefault("cwd", git_repo.path)
    check = kwargs.pop("check", False)
    return subprocess.run(  # nosec B603  # noqa: S603 - argv is assembled from test fixture paths only; shell=False; exercises real git in a temp repo, not user input
        [git_repo.git_bin, *args],
        check=check,
        **kwargs,
    )


@pytest.fixture
def git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GitRepo:
    """Initialize a temporary git repository and chdir into it."""
    git_bin = shutil.which("git")
    if git_bin is None:
        pytest.skip("git is required for review context integration tests")
    if shutil.which("bash") is None:
        pytest.skip("bash is required for git-backed review context integration tests")
    monkeypatch.chdir(tmp_path)
    init = _run_git(
        GitRepo(path=tmp_path, git_bin=git_bin),
        "init",
        "-b",
        "main",
        check=False,
        capture_output=True,
    )
    if init.returncode != 0:
        repo = GitRepo(path=tmp_path, git_bin=git_bin)
        _run_git(repo, "init", check=True, capture_output=True)
        _run_git(repo, "checkout", "-b", "main", check=True, capture_output=True)
    hooks_dir = tmp_path / ".git-hooks"
    hooks_dir.mkdir()
    repo = GitRepo(path=tmp_path, git_bin=git_bin)
    _run_git(repo, "config", "user.email", "test@test.com", check=True)
    _run_git(repo, "config", "user.name", "Test User", check=True)
    _run_git(repo, "config", "commit.gpgsign", "false", check=True)
    _run_git(repo, "config", "tag.gpgSign", "false", check=True)
    _run_git(repo, "config", "core.hooksPath", str(hooks_dir), check=True)
    _run_git(repo, "config", "diff.renames", "true", check=True)
    readme = tmp_path / "README.md"
    readme.write_text("init\n", encoding="utf-8")
    _run_git(repo, "add", "README.md", check=True)
    _run_git(repo, "commit", "-m", "init", check=True, capture_output=True)
    return repo


def test_collect_uncommitted_context_uses_real_git(git_repo: GitRepo) -> None:
    """Uncommitted mode collects working-tree diffs via real git subprocesses."""
    tracked = git_repo / "tracked.py"
    tracked.write_text("old\n", encoding="utf-8")
    _run_git(git_repo, "add", "tracked.py", check=True)
    _run_git(git_repo, "commit", "-m", "add tracked", check=True, capture_output=True)
    tracked.write_text("new\n", encoding="utf-8")

    context = collect_review_context(uncommitted=True)

    assert_that(context.base_ref).is_not_empty()
    assert_that(context.head_ref).is_equal_to("WORKTREE")
    assert_that(context.changed_files).extracting("path").contains("tracked.py")
    assert_that(context.unified_diff).contains("tracked.py")


def test_collect_uncommitted_context_includes_staged_and_unstaged(
    git_repo: GitRepo,
) -> None:
    """Uncommitted mode merges staged index changes and working-tree edits."""
    staged = git_repo / "staged.py"
    unstaged = git_repo / "unstaged.py"
    staged.write_text("initial\n", encoding="utf-8")
    unstaged.write_text("initial\n", encoding="utf-8")
    _run_git(git_repo, "add", "staged.py", "unstaged.py", check=True)
    _run_git(git_repo, "commit", "-m", "add files", check=True, capture_output=True)

    staged.write_text("staged edit\n", encoding="utf-8")
    unstaged.write_text("unstaged edit\n", encoding="utf-8")
    _run_git(git_repo, "add", "staged.py", check=True)

    context = collect_review_context(uncommitted=True)

    assert_that({file.path for file in context.changed_files}).is_equal_to(
        {"staged.py", "unstaged.py"},
    )
    assert_that(context.unified_diff).contains("staged.py")
    assert_that(context.unified_diff).contains("unstaged.py")


def test_collect_branch_context_uses_real_git(git_repo: GitRepo) -> None:
    """Branch mode collects merge-base diffs via real git subprocesses."""
    tracked = git_repo / "base.py"
    tracked.write_text("a\n", encoding="utf-8")
    _run_git(git_repo, "add", "base.py", check=True)
    _run_git(git_repo, "commit", "-m", "add base", check=True, capture_output=True)
    _run_git(git_repo, "checkout", "-b", "feature", check=True, capture_output=True)

    feature = git_repo / "feature.py"
    feature.write_text("b\n", encoding="utf-8")
    _run_git(git_repo, "add", "feature.py", check=True)
    _run_git(git_repo, "commit", "-m", "add feature", check=True, capture_output=True)

    context = collect_review_context(base="main")

    assert_that(context.changed_files).extracting("path").contains("feature.py")
    assert_that(context.unified_diff).contains("feature.py")


def test_collect_and_chunk_rename_uses_consistent_paths(git_repo: GitRepo) -> None:
    """Real git rename output aligns changed_files paths with diff sections."""
    original = git_repo / "pkg" / "old.py"
    original.parent.mkdir(parents=True)
    original.write_text("old\n", encoding="utf-8")
    _run_git(git_repo, "add", "pkg/old.py", check=True)
    _run_git(git_repo, "commit", "-m", "add original", check=True, capture_output=True)
    _run_git(git_repo, "checkout", "-b", "feature", check=True, capture_output=True)
    (git_repo / "lib").mkdir()
    _run_git(git_repo, "mv", "pkg/old.py", "lib/new.py", check=True)
    _run_git(git_repo, "commit", "-m", "rename", check=True, capture_output=True)

    context = collect_review_context(base="main")
    result = prepare_review_chunks(context=context, max_tokens=10_000)

    chunked_paths = {path for chunk in result.chunks for path in chunk.files}
    assert_that(chunked_paths).contains("lib/new.py")
    assert_that(chunked_paths).does_not_contain("pkg/old.py")
    assert_that(context.changed_files).extracting("path").contains("lib/new.py")
    assert_that(context.changed_files).extracting("path").does_not_contain("pkg/old.py")
