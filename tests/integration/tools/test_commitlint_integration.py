"""Integration tests for the commitlint tool (commit-message linter).

These tests exercise the real ``commitlint`` binary against throwaway git
repositories. They are skipped when ``commitlint`` or ``git`` is unavailable.
"""

from __future__ import annotations

import subprocess  # nosec B404 - test helper, fixed args, no shell
from pathlib import Path

from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.parsers.commitlint.commitlint_issue import CommitlintIssue
from lintro.plugins import ToolRegistry
from tests.integration._tools import require_tool
from tests.test_samples_helpers import sample_path

_SELF_CONTAINED_CONFIG = (
    "module.exports = {\n"
    "  rules: {\n"
    "    'type-empty': [2, 'never'],\n"
    "    'subject-empty': [2, 'never'],\n"
    "  },\n"
    "};\n"
)

pytestmark = [
    require_tool("commitlint"),
    require_tool("git"),
]


def _git(repo: Path, *args: str) -> None:
    """Run a git command inside a repository.

    Args:
        repo: Repository working directory.
        *args: Git arguments.
    """
    subprocess.run(  # nosec B603 B607 - fixed argv run against git in a controlled test; binary name resolved from PATH, not attacker-controlled; shell=False, no user shell input
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )


def _init_repo(repo: Path, *, config: str | None) -> None:
    """Initialise a git repository with an optional commitlint config.

    Args:
        repo: Directory to initialise.
        config: commitlint config JS source, or None for no config.
    """
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    if config is not None:
        (repo / "commitlint.config.js").write_text(config, encoding="utf-8")


def _sample_message(name: str) -> str:
    """Load a commitlint sample message.

    Args:
        name: Filename under the commitlint git sample directory.

    Returns:
        Commit message text with fixture leading/trailing whitespace removed.
    """
    return (
        sample_path("tools", "git", "commitlint", name)
        .read_text(
            encoding="utf-8",
        )
        .strip()
    )


def test_commitlint_detects_bad_last_commit(tmp_path: Path) -> None:
    """Commitlint flags a non-conventional last commit message."""
    _init_repo(tmp_path, config=_SELF_CONTAINED_CONFIG)
    (tmp_path / "a.txt").write_text("x\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", _sample_message("commitlint_violations.txt"))

    tool = ToolRegistry.get("commitlint")
    tool.exclude_patterns = []
    result: ToolResult = tool.check([str(tmp_path)], {})

    assert_that(result.name).is_equal_to("commitlint")
    assert_that(result.success).is_false()
    assert_that(result.issues_count > 0).is_true()
    issues = [i for i in (result.issues or []) if isinstance(i, CommitlintIssue)]
    assert_that([i.rule for i in issues]).contains("subject-empty")


def test_commitlint_passes_conventional_commit(tmp_path: Path) -> None:
    """Commitlint accepts a conventional last commit message."""
    _init_repo(tmp_path, config=_SELF_CONTAINED_CONFIG)
    (tmp_path / "a.txt").write_text("x\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", _sample_message("commitlint_clean.txt"))

    tool = ToolRegistry.get("commitlint")
    tool.exclude_patterns = []
    result: ToolResult = tool.check([str(tmp_path)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_commitlint_skips_without_config(tmp_path: Path) -> None:
    """Commitlint is skipped (non-error) when no config is present."""
    _init_repo(tmp_path, config=None)
    (tmp_path / "a.txt").write_text("x\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "anything at all")

    tool = ToolRegistry.get("commitlint")
    tool.exclude_patterns = []
    result: ToolResult = tool.check([str(tmp_path)], {})

    assert_that(result.skipped).is_true()
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
