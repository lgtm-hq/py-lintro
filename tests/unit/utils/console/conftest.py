"""Shared fixtures and helpers for the console logger tests.

Console output is gated on an interactive terminal in two ways: click strips
ANSI colour when the stream is not a TTY, and the decorative ASCII art is
skipped entirely. Both make the real behaviour invisible under plain pytest
capture, so the helpers here substitute an in-memory interactive stream and a
deterministic art asset. That lets the tests assert on emitted output instead
of on mock call bookkeeping (#2315).
"""

from __future__ import annotations

import io
import sys
from collections.abc import Generator
from pathlib import Path

import pytest
from loguru import logger as loguru_logger

import lintro.utils.display_helpers as display_helpers
from lintro.utils.console.logger import ThreadSafeConsoleLogger


class FakeTty(io.StringIO):
    """An in-memory stream that claims to be an interactive terminal."""

    def isatty(self) -> bool:
        """Report the stream as interactive.

        Returns:
            Always ``True``.
        """
        return True


def patch_tty_streams(
    *,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[FakeTty, FakeTty]:
    """Replace stdout and stderr with in-memory interactive streams.

    Call this from the test body rather than from a fixture: pytest's global
    capture rebinds ``sys.stdout``/``sys.stderr`` when the call phase starts,
    which would undo a substitution made during setup.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        The substituted ``(stdout, stderr)`` streams.
    """
    stdout = FakeTty()
    stderr = FakeTty()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    return stdout, stderr


@pytest.fixture
def labelled_ascii_art(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the ASCII art assets with a deterministic label per file.

    The real art is one of several randomly chosen sections, so the emitted
    text cannot be asserted directly. Substituting ``ART:<filename>`` keeps
    assertions on observable output while still proving which asset the issue
    count selected.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """

    def _fake_read(filename: str) -> list[str]:
        return [f"ART:{filename}"]

    monkeypatch.setattr(display_helpers, "read_ascii_art", _fake_read)


@pytest.fixture
def loguru_messages() -> Generator[list[str]]:
    """Capture every message loguru emits during the test.

    Some logger methods deliberately write to loguru instead of the console;
    this sink makes that observable without patching loguru itself.

    Yields:
        list[str]: A list that accumulates the formatted log messages.
    """
    captured: list[str] = []
    sink_id = loguru_logger.add(
        lambda message: captured.append(message.record["message"]),
        level="DEBUG",
    )
    try:
        yield captured
    finally:
        loguru_logger.remove(sink_id)


@pytest.fixture
def logger() -> ThreadSafeConsoleLogger:
    """Provide a default ThreadSafeConsoleLogger instance.

    Returns:
        A ThreadSafeConsoleLogger with no run directory configured.
    """
    return ThreadSafeConsoleLogger()


@pytest.fixture
def logger_with_run_dir(tmp_path: Path) -> ThreadSafeConsoleLogger:
    """Provide a ThreadSafeConsoleLogger with a run directory configured.

    Args:
        tmp_path: Pytest temporary directory fixture.

    Returns:
        A ThreadSafeConsoleLogger configured with a run directory.
    """
    return ThreadSafeConsoleLogger(run_dir=tmp_path)
