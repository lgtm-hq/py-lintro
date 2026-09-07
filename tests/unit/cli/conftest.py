"""Shared fixtures for CLI tests."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from tests.constants import EXIT_SUCCESS


@dataclass
class RecordedLintRun:
    """Plain stand-in for ``run_lint_with_ai`` that records its arguments.

    The check and format commands exist to translate a Click invocation into
    one pipeline call, so the observable result of running them is the
    argument mapping that reaches the pipeline plus the exit code the command
    returns. Recording that mapping in a real list keeps the assertions about
    data rather than about how a mock was called (#2315).

    Attributes:
        exit_code: Value returned to the command, so tests can drive the
            command's own exit status.
        calls: One entry per invocation, holding the keyword arguments.
    """

    exit_code: int = EXIT_SUCCESS
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __call__(self, **kwargs: Any) -> int:
        """Record one pipeline invocation.

        Args:
            **kwargs: Keyword arguments the command passed to the pipeline.

        Returns:
            The configured exit code.
        """
        self.calls.append(dict(kwargs))
        return self.exit_code


@pytest.fixture
def recorded_check_run(monkeypatch: pytest.MonkeyPatch) -> RecordedLintRun:
    """Replace the check command's pipeline call with a recorder.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        The recorder installed in place of ``run_lint_with_ai``.
    """
    recorder = RecordedLintRun()
    monkeypatch.setattr(
        "lintro.cli_utils.commands.check.run_lint_with_ai",
        recorder,
    )
    return recorder


@pytest.fixture
def recorded_format_run(monkeypatch: pytest.MonkeyPatch) -> RecordedLintRun:
    """Replace the format command's pipeline call with a recorder.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        The recorder installed in place of ``run_lint_with_ai``.
    """
    recorder = RecordedLintRun()
    monkeypatch.setattr(
        "lintro.cli_utils.commands.format.run_lint_with_ai",
        recorder,
    )
    return recorder


@pytest.fixture
def isolated_cli_runner() -> CliRunner:
    """Click CLI test runner with isolated filesystem.

    Returns:
        A CliRunner instance with isolated filesystem.
    """
    # click >= 8.2 removed the ``mix_stderr`` argument; stdout and stderr are
    # always captured separately, which is the behaviour this fixture wants.
    return CliRunner()


@pytest.fixture
def mock_run_lint_with_ai() -> Generator[tuple[MagicMock, MagicMock], None, None]:
    """Mock the run_lint_with_ai function used by check/format commands.

    Yields:
        tuple[MagicMock, MagicMock]: Check and format mock instances.
    """
    with (
        patch("lintro.cli_utils.commands.check.run_lint_with_ai") as mock_check,
        patch(
            "lintro.cli_utils.commands.format.run_lint_with_ai",
        ) as mock_format,
    ):
        # Configure both mocks to return 0 by default
        mock_check.return_value = EXIT_SUCCESS
        mock_format.return_value = EXIT_SUCCESS
        yield mock_check, mock_format


@pytest.fixture
def mock_tool_registry() -> Generator[MagicMock, None, None]:
    """Mock ToolRegistry with common tools.

    Yields:
        MagicMock: A MagicMock instance for the ToolRegistry.
    """
    with patch("lintro.plugins.registry.ToolRegistry") as mock:
        mock.get_names.return_value = ["ruff", "black", "mypy", "pytest"]
        mock.is_registered.return_value = True
        mock.get.return_value = MagicMock()
        yield mock


@pytest.fixture
def mock_subprocess_success() -> Generator[MagicMock, None, None]:
    """Mock successful subprocess execution.

    Yields:
        MagicMock: A MagicMock instance for subprocess.run.
    """
    with patch("subprocess.run") as mock:
        mock.return_value = MagicMock(
            returncode=EXIT_SUCCESS,
            stdout="",
            stderr="",
        )
        yield mock
