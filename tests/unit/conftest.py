"""Shared fixtures for unit tests."""

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import run_git


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


@pytest.fixture
def ai_config_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[[str], Path]]:
    """Return a writer for a project whose own config file alone is resolved.

    Shared by any config-resolution test under ``tests/unit``. The user tier
    (home, ``LINTRO_GLOBAL_CONFIG``, ``XDG_CONFIG_HOME``), ``LINTRO_AI_*``
    overlays and the memoised config are isolated, so a command run
    afterwards resolves only the ``.lintro-config.yaml`` the writer put in
    the project.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    Yields:
        Callable[[str], Path]: A writer taking the full config text and
        returning the project root, already the working directory.
    """
    # Lazy, like evict_foreign_config_singleton in tests/conftest.py; do not hoist.
    from lintro.config.config_loader import clear_config_cache

    home = tmp_path / "home"
    home.mkdir()
    for name in [key for key in os.environ if key.startswith("LINTRO_AI_")]:
        monkeypatch.delenv(name)
    monkeypatch.delenv("LINTRO_GLOBAL_CONFIG", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    root = (tmp_path / "project").resolve()
    root.mkdir()
    monkeypatch.chdir(root)

    def _write(config: str) -> Path:
        (root / ".lintro-config.yaml").write_text(config, encoding="utf-8")
        clear_config_cache()
        return root

    yield _write
    clear_config_cache()
