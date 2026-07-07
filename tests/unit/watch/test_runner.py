"""Tests for the watch-mode runner.

The execution backend (``run_lint_tools_simple``) is injected as a fake so
these tests assert the runner's orchestration — file filtering, action
selection, headers, screen clearing — without invoking real tools.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.watch.runner import WatchRunner


@pytest.fixture
def recorder() -> dict[str, Any]:
    """Provide a mutable record for the fake execution backend.

    Returns:
        A dict populated by the fake ``run_tools`` with the last call kwargs.
    """
    return {}


def _make_run_tools(recorder: dict[str, Any], exit_code: int = 0):
    """Build a fake ``run_tools`` that records its kwargs.

    Args:
        recorder: Dict to store the received kwargs and call count.
        exit_code: Exit code the fake returns.

    Returns:
        A callable matching the ``run_lint_tools_simple`` keyword contract.
    """
    recorder["calls"] = 0

    def _run(**kwargs: Any) -> int:
        recorder["calls"] += 1
        recorder["kwargs"] = kwargs
        return exit_code

    return _run


def test_run_batch_checks_by_default(
    tmp_path: Path,
    recorder: dict[str, Any],
) -> None:
    """Without --fix, the runner uses the CHECK action."""
    target = tmp_path / "foo.py"
    target.write_text("x = 1\n")
    lines: list[str] = []

    runner = WatchRunner(
        emit=lines.append,
        run_tools=_make_run_tools(recorder),
    )
    runner.run_batch({str(target)})

    assert_that(recorder["calls"]).is_equal_to(1)
    assert_that(recorder["kwargs"]["action"]).is_equal_to(Action.CHECK)
    assert_that(recorder["kwargs"]["paths"]).contains(str(target))


def test_run_batch_fixes_when_auto_fix(
    tmp_path: Path,
    recorder: dict[str, Any],
) -> None:
    """With auto_fix, the runner uses the FIX action."""
    target = tmp_path / "foo.py"
    target.write_text("x = 1\n")

    runner = WatchRunner(
        auto_fix=True,
        emit=lambda _line: None,
        run_tools=_make_run_tools(recorder),
    )
    runner.run_batch({str(target)})

    assert_that(recorder["kwargs"]["action"]).is_equal_to(Action.FIX)


def test_run_batch_skips_nonexistent_files(
    tmp_path: Path,
    recorder: dict[str, Any],
) -> None:
    """Deleted/nonexistent paths do not trigger a run."""
    runner = WatchRunner(
        emit=lambda _line: None,
        run_tools=_make_run_tools(recorder),
    )

    result = runner.run_batch({str(tmp_path / "gone.py")})

    assert_that(result).is_equal_to(0)
    assert_that(recorder["calls"]).is_equal_to(0)


def test_run_batch_reports_no_matching_tools(
    tmp_path: Path,
    recorder: dict[str, Any],
) -> None:
    """When selection yields no tools, emit a notice and do not execute.

    A ``--tools`` allowlist that matches nothing for the changed file drives
    the empty-selection branch deterministically.
    """
    target = tmp_path / "foo.py"
    target.write_text("x = 1\n")
    lines: list[str] = []

    runner = WatchRunner(
        restrict_to=["definitely-not-a-real-tool"],
        emit=lines.append,
        run_tools=_make_run_tools(recorder),
    )
    result = runner.run_batch({str(target)})

    assert_that(result).is_equal_to(0)
    assert_that(recorder["calls"]).is_equal_to(0)
    assert_that(any("no matching tools" in line for line in lines)).is_true()


def test_run_batch_prints_timestamped_header(
    tmp_path: Path,
    recorder: dict[str, Any],
) -> None:
    """A run prints a header line mentioning the changed file."""
    target = tmp_path / "foo.py"
    target.write_text("x = 1\n")
    lines: list[str] = []

    runner = WatchRunner(
        emit=lines.append,
        run_tools=_make_run_tools(recorder),
    )
    runner.run_batch({str(target)})

    header = next((line for line in lines if line.startswith("[")), None)
    assert_that(header).is_not_none()
    assert_that(header).contains("foo.py")


def test_run_batch_clears_screen_when_enabled(
    tmp_path: Path,
    recorder: dict[str, Any],
) -> None:
    """clear_screen emits an ANSI clear sequence before the header."""
    target = tmp_path / "foo.py"
    target.write_text("x = 1\n")
    lines: list[str] = []

    runner = WatchRunner(
        clear_screen=True,
        emit=lines.append,
        run_tools=_make_run_tools(recorder),
    )
    runner.run_batch({str(target)})

    assert_that(any("\033[2J" in line for line in lines)).is_true()


def test_run_batch_propagates_exit_code(
    tmp_path: Path,
    recorder: dict[str, Any],
) -> None:
    """The runner surfaces the backend exit code via last_exit_code."""
    target = tmp_path / "foo.py"
    target.write_text("x = 1\n")

    runner = WatchRunner(
        emit=lambda _line: None,
        run_tools=_make_run_tools(recorder, exit_code=1),
    )
    result = runner.run_batch({str(target)})

    assert_that(result).is_equal_to(1)
    assert_that(runner.last_exit_code).is_equal_to(1)


def test_run_batch_forwards_restrict_to(
    tmp_path: Path,
    recorder: dict[str, Any],
) -> None:
    """A --tools allowlist limits the tools passed to the backend."""
    target = tmp_path / "foo.py"
    target.write_text("x = 1\n")

    runner = WatchRunner(
        restrict_to=["ruff"],
        emit=lambda _line: None,
        run_tools=_make_run_tools(recorder),
    )
    runner.run_batch({str(target)})

    assert_that(recorder["kwargs"]["tools"]).is_equal_to("ruff")
