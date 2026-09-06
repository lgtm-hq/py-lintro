"""Unit tests for ThreadSafeConsoleLogger header display methods.

Tests cover the lintro header, tool header, and post-checks header
formatting and display functionality.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.utils.console.logger import ThreadSafeConsoleLogger
from tests.unit.utils.console.conftest import patch_tty_streams


def test_print_lintro_header_with_run_dir(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The header announces where the run's output files will be written.

    Args:
        tmp_path: Temporary directory path for test files.
        capsys: Pytest stdout/stderr capture fixture.
    """
    logger = ThreadSafeConsoleLogger(run_dir=tmp_path)

    logger.print_lintro_header()

    out = capsys.readouterr().out
    assert_that(out).contains("[LINTRO]")
    assert_that(out).contains(str(tmp_path))
    assert_that(out).ends_with("\n\n")


def test_print_lintro_header_without_run_dir_is_noop(
    logger: ThreadSafeConsoleLogger,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """With no run directory there is nothing to announce, so nothing prints.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
        capsys: Pytest stdout/stderr capture fixture.
    """
    logger.print_lintro_header()

    assert_that(capsys.readouterr().out).is_empty()
    assert_that(logger.get_buffer()).is_empty()


def test_print_tool_header_outputs_formatted_header(
    logger: ThreadSafeConsoleLogger,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The tool banner names the tool and action between two borders.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
        capsys: Pytest stdout/stderr capture fixture.
    """
    logger.print_tool_header("ruff", "check")

    lines = capsys.readouterr().out.splitlines()
    assert_that(lines).is_length(4)
    assert_that(lines[0]).matches(r"^=+$")
    assert_that(lines[1]).contains("Running ruff (check)")
    assert_that(lines[2]).is_equal_to(lines[0])
    assert_that(lines[3]).is_empty()


@pytest.mark.parametrize(
    ("tool_name", "action"),
    [
        pytest.param("ruff", "check", id="ruff-check"),
        pytest.param("black", "fmt", id="black-fmt"),
        pytest.param("mypy", "check", id="mypy-check"),
        pytest.param("pytest", "test", id="pytest-test"),
    ],
)
def test_print_tool_header_various_tools(
    logger: ThreadSafeConsoleLogger,
    tool_name: str,
    action: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Every tool and action pair is named in its own banner.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
        tool_name: The name of the tool to display.
        action: The action being performed.
        capsys: Pytest stdout/stderr capture fixture.
    """
    logger.print_tool_header(tool_name, action)

    lines = capsys.readouterr().out.splitlines()
    assert_that(lines).is_length(4)
    assert_that(lines[1]).contains(f"Running {tool_name} ({action})")


def test_print_post_checks_header_outputs_styled_header(
    logger: ThreadSafeConsoleLogger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The post-checks banner uses magenta heavy borders around its own title.

    The distinct style is what separates optional follow-up checks from the
    primary tool runs, so the colour is part of the behaviour.

    Args:
        logger: ThreadSafeConsoleLogger instance fixture.
        monkeypatch: Pytest monkeypatch fixture.
    """
    stdout, _ = patch_tty_streams(monkeypatch=monkeypatch)

    logger.print_post_checks_header()

    lines = stdout.getvalue().splitlines()
    assert_that(lines).is_length(5)
    assert_that(lines[0]).contains("\u2501")
    assert_that(lines[0]).starts_with("\x1b[35m")
    assert_that(lines[1]).contains("POST-CHECKS")
    assert_that(lines[2]).contains("Running optional follow-up checks")
    assert_that(lines[3]).is_equal_to(lines[0])
    assert_that(lines[4]).is_empty()
