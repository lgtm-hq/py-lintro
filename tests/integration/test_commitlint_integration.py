"""Integration tests for the commitlint tool (commit-message linter).

These tests exercise the real ``commitlint`` binary against throwaway git
repositories. They are skipped when ``commitlint`` or ``git`` is unavailable,
or when the installed commitlint version is below the manifest minimum.
"""

from __future__ import annotations

import re
import shutil
import subprocess  # nosec B404 - test helper, fixed args, no shell
from pathlib import Path

import pytest
from assertpy import assert_that
from packaging.version import Version

from lintro.models.core.tool_result import ToolResult
from lintro.parsers.commitlint.commitlint_issue import CommitlintIssue
from lintro.plugins import ToolRegistry

_SELF_CONTAINED_CONFIG = (
    "module.exports = {\n"
    "  rules: {\n"
    "    'type-empty': [2, 'never'],\n"
    "    'subject-empty': [2, 'never'],\n"
    "  },\n"
    "};\n"
)

_COMMITLINT_MIN_VERSION = Version("21.2.1")


def _get_commitlint_version() -> Version | None:
    """Return the installed commitlint version, if available.

    Returns:
        Parsed version, or None when commitlint is missing/unparseable.
    """
    if shutil.which("commitlint") is None:
        return None
    result = subprocess.run(  # nosec B603 B607 - fixed argv against commitlint on PATH
        ["commitlint", "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    match = re.search(r"(\d+\.\d+\.\d+)", result.stdout + result.stderr)
    if match is None:
        return None
    return Version(match.group(1))


_installed_commitlint_version = _get_commitlint_version()

_requires_binaries = pytest.mark.skipif(
    shutil.which("git") is None
    or _installed_commitlint_version is None
    or _installed_commitlint_version < _COMMITLINT_MIN_VERSION,
    reason=(
        f"commitlint >= {_COMMITLINT_MIN_VERSION} or git not installed "
        f"(found: {_installed_commitlint_version})"
    ),
)


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


@_requires_binaries
def test_commitlint_detects_bad_last_commit(tmp_path: Path) -> None:
    """Commitlint flags a non-conventional last commit message."""
    _init_repo(tmp_path, config=_SELF_CONTAINED_CONFIG)
    (tmp_path / "a.txt").write_text("x\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "Bad Commit Message Without Type")

    tool = ToolRegistry.get("commitlint")
    tool.exclude_patterns = []
    result: ToolResult = tool.check([str(tmp_path)], {})

    assert_that(result.name).is_equal_to("commitlint")
    assert_that(result.success).is_false()
    assert_that(result.issues_count > 0).is_true()
    issues = [i for i in (result.issues or []) if isinstance(i, CommitlintIssue)]
    assert_that([i.rule for i in issues]).contains("subject-empty")


@_requires_binaries
def test_commitlint_passes_conventional_commit(tmp_path: Path) -> None:
    """Commitlint accepts a conventional last commit message."""
    _init_repo(tmp_path, config=_SELF_CONTAINED_CONFIG)
    (tmp_path / "a.txt").write_text("x\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "feat(scope): add a thing")

    tool = ToolRegistry.get("commitlint")
    tool.exclude_patterns = []
    result: ToolResult = tool.check([str(tmp_path)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


@_requires_binaries
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
