"""Integration tests for review context collection with real git."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.ai.review.context import collect_review_context


@pytest.fixture
def git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Initialize a temporary git repository and chdir into it."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True)
    readme = tmp_path / "README.md"
    readme.write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], check=True)
    subprocess.run(["git", "commit", "-m", "init"], check=True, capture_output=True)
    return tmp_path


def test_collect_uncommitted_context_uses_real_git(git_repo: Path) -> None:
    """Uncommitted mode collects working-tree diffs via real git subprocesses."""
    tracked = git_repo / "tracked.py"
    tracked.write_text("old\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], check=True)
    subprocess.run(
        ["git", "commit", "-m", "add tracked"],
        check=True,
        capture_output=True,
    )
    tracked.write_text("new\n", encoding="utf-8")

    context = collect_review_context(uncommitted=True)

    assert_that(context.base_ref).is_equal_to("WORKTREE")
    assert_that(context.changed_files).extracting("path").contains("tracked.py")
    assert_that(context.unified_diff).contains("tracked.py")


def test_collect_branch_context_uses_real_git(git_repo: Path) -> None:
    """Branch mode collects merge-base diffs via real git subprocesses."""
    tracked = git_repo / "base.py"
    tracked.write_text("a\n", encoding="utf-8")
    subprocess.run(["git", "add", "base.py"], check=True)
    subprocess.run(["git", "commit", "-m", "add base"], check=True, capture_output=True)
    subprocess.run(
        ["git", "checkout", "-b", "feature"],
        check=True,
        capture_output=True,
    )

    feature = git_repo / "feature.py"
    feature.write_text("b\n", encoding="utf-8")
    subprocess.run(["git", "add", "feature.py"], check=True)
    subprocess.run(
        ["git", "commit", "-m", "add feature"],
        check=True,
        capture_output=True,
    )

    context = collect_review_context(base="main")

    assert_that(context.changed_files).extracting("path").contains("feature.py")
    assert_that(context.unified_diff).contains("feature.py")
