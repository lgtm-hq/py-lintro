"""Shared fixtures for unit tests."""

import os
import subprocess  # nosec B404 - drives git in temp test repos; shell=False
from pathlib import Path
from typing import Any

import pytest


class FakeLogger:
    """Minimal logger stub capturing method calls for assertions."""

    def __init__(self) -> None:
        """Initialize the fake logger with call storage and run dir."""
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.run_dir = ".lintro/test"

    def _rec(self, name: str, *a: Any, **k: Any) -> None:
        self.calls.append((name, a, k))

    def info(self, *a: Any, **k: Any) -> None:
        """Record an info call.

        Args:
            *a: Positional arguments passed to the logger.
            **k: Keyword arguments passed to the logger.
        """
        self._rec("info", *a, **k)

    def debug(self, *a: Any, **k: Any) -> None:
        """Record a debug call.

        Args:
            *a: Positional arguments passed to the logger.
            **k: Keyword arguments passed to the logger.
        """
        self._rec("debug", *a, **k)

    def warning(self, *a: Any, **k: Any) -> None:
        """Record a warning call.

        Args:
            *a: Positional arguments passed to the logger.
            **k: Keyword arguments passed to the logger.
        """
        self._rec("warning", *a, **k)

    def error(self, *a: Any, **k: Any) -> None:
        """Record an error call.

        Args:
            *a: Positional arguments passed to the logger.
            **k: Keyword arguments passed to the logger.
        """
        self._rec("error", *a, **k)

    def success(self, *a: Any, **k: Any) -> None:
        """Record a success call.

        Args:
            *a: Positional arguments passed to the logger.
            **k: Keyword arguments passed to the logger.
        """
        self._rec("success", *a, **k)

    def console_output(self, *a: Any, **k: Any) -> None:
        """Record console output.

        Args:
            *a: Positional arguments passed to the logger.
            **k: Keyword arguments passed to the logger.
        """
        self._rec("console_output", *a, **k)

    def print_lintro_header(self, *a: Any, **k: Any) -> None:
        """Record header printing.

        Args:
            *a: Positional arguments passed to the logger.
            **k: Keyword arguments passed to the logger.
        """
        self._rec("print_lintro_header", *a, **k)

    def print_verbose_info(self, *a: Any, **k: Any) -> None:
        """Record verbose info printing.

        Args:
            *a: Positional arguments passed to the logger.
            **k: Keyword arguments passed to the logger.
        """
        self._rec("print_verbose_info", *a, **k)

    def print_tool_header(self, *a: Any, **k: Any) -> None:
        """Record tool header printing.

        Args:
            *a: Positional arguments passed to the logger.
            **k: Keyword arguments passed to the logger.
        """
        self._rec("print_tool_header", *a, **k)

    def print_tool_result(self, *a: Any, **k: Any) -> None:
        """Record tool result printing.

        Args:
            *a: Positional arguments passed to the logger.
            **k: Keyword arguments passed to the logger.
        """
        self._rec("print_tool_result", *a, **k)

    def print_execution_summary(self, *a: Any, **k: Any) -> None:
        """Record execution summary printing.

        Args:
            *a: Positional arguments passed to the logger.
            **k: Keyword arguments passed to the logger.
        """
        self._rec("print_execution_summary", *a, **k)

    def print_post_checks_header(self, *a: Any, **k: Any) -> None:
        """Record post checks header printing.

        Args:
            *a: Positional arguments passed to the logger.
            **k: Keyword arguments passed to the logger.
        """
        self._rec("print_post_checks_header", *a, **k)

    def save_console_log(self, *a: Any, **k: Any) -> None:
        """Record console log saving.

        Args:
            *a: Positional arguments passed to the logger.
            **k: Keyword arguments passed to the logger.
        """
        self._rec("save_console_log", *a, **k)


@pytest.fixture
def fake_logger() -> FakeLogger:
    """Provide a FakeLogger instance for testing.

    Returns:
        FakeLogger: Configured FakeLogger instance for unit testing.
    """
    return FakeLogger()


def run_git(cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a git command in a temp repo, isolated from developer git config.

    A global ``commit.gpgsign``, ``core.hooksPath`` or ``init.templateDir``
    would otherwise change how these fixtures behave from machine to machine,
    and an exported ``GIT_INDEX_FILE`` would point git at the wrong index.

    Args:
        cmd: Full argv, starting with ``git``.
        cwd: Working directory.

    Returns:
        The completed process.
    """
    env = os.environ.copy()
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    for leaked in ("GIT_INDEX_FILE", "GIT_DIR", "GIT_WORK_TREE"):
        env.pop(leaked, None)
    return (
        subprocess.run(  # nosec B603 B607 - fixed git argv in a temp repo; shell=False
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
    )


def init_git_repo(tmp_path: Path, *, files: dict[str, str]) -> Path:
    """Create a temp git repo on ``main`` with ``files`` committed.

    Args:
        tmp_path: Directory to turn into a repository.
        files: Repo-relative path to contents for the initial commit.

    Returns:
        The repository root.
    """
    run_git(["git", "init"], cwd=tmp_path)
    run_git(["git", "config", "user.email", "test@example.com"], cwd=tmp_path)
    run_git(["git", "config", "user.name", "Test User"], cwd=tmp_path)
    # Pin the branch name rather than depending on the host's init.defaultBranch.
    run_git(["git", "checkout", "-b", "main"], cwd=tmp_path)
    for rel, contents in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")
    run_git(["git", "add", *files], cwd=tmp_path)
    run_git(["git", "commit", "-m", "init"], cwd=tmp_path)
    return tmp_path
