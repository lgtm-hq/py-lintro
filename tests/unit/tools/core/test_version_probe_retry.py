"""Tests for the version probe retry on timeout (#3083)."""

from __future__ import annotations

import subprocess  # nosec B404 - only TimeoutExpired/CompletedProcess are used
from collections.abc import Callable, Generator, Sequence
from typing import Any

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.tools.core.version_parsing import (
    _run_probe_with_retry,
    check_tool_version,
    get_minimum_versions,
)

_SEMGREP_ARGV = ["semgrep", "--version"]

#: One scripted outcome per attempt: an exception to raise or stdout to return.
_Outcome = BaseException | str


@pytest.fixture
def warnings() -> Generator[list[str], None, None]:
    """Collect loguru WARNING-level messages emitted during a test.

    Yields:
        list[str]: Messages logged at WARNING or above.
    """
    messages: list[str] = []
    sink_id = logger.add(
        lambda message: messages.append(str(message).strip()),
        level="WARNING",
        format="{message}",
    )
    try:
        yield messages
    finally:
        logger.remove(sink_id)


@pytest.fixture
def scripted_run(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[list[_Outcome]], list[float]]:
    """Replace ``subprocess.run`` for the version probe with scripted outcomes.

    Args:
        monkeypatch: Pytest fixture for patching modules and attributes.

    Returns:
        Callable[[list[_Outcome]], list[float]]: Installs the outcomes and
            returns the list that receives each attempt's ``timeout``.
    """

    def install(outcomes: list[_Outcome]) -> list[float]:
        budgets: list[float] = []
        pending = list(outcomes)

        def fake_run(
            cmd: Sequence[str],
            **kwargs: Any,
        ) -> subprocess.CompletedProcess[str]:
            assert_that(kwargs.get("shell", False)).is_false()
            budgets.append(kwargs["timeout"])
            outcome = pending.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return subprocess.CompletedProcess(list(cmd), 0, outcome, "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        return budgets

    return install


def _timeout(seconds: float) -> subprocess.TimeoutExpired:
    """Build the exception ``subprocess.run`` raises for a slow probe.

    Args:
        seconds: Budget the probe exceeded.

    Returns:
        subprocess.TimeoutExpired: Exception for the semgrep probe.
    """
    return subprocess.TimeoutExpired(_SEMGREP_ARGV, seconds)


def test_probe_timeout_then_success_parses_version_with_doubled_budget(
    monkeypatch: pytest.MonkeyPatch,
    scripted_run: Callable[[list[_Outcome]], list[float]],
    warnings: list[str],
) -> None:
    """A probe that times out once is retried with double the budget.

    Args:
        monkeypatch: Pytest fixture for patching modules and attributes.
        scripted_run: Installs scripted ``subprocess.run`` outcomes.
        warnings: Captured WARNING-level log messages.
    """
    monkeypatch.setenv("LINTRO_VERSION_TIMEOUT", "30")
    semgrep_min = get_minimum_versions()["semgrep"]
    budgets = scripted_run([_timeout(30), f"{semgrep_min}\n"])

    info = check_tool_version("semgrep", ["semgrep"])

    assert_that(info.current_version).is_equal_to(semgrep_min)
    assert_that(info.probe_failed).is_false()
    assert_that(info.error_message).is_none()
    assert_that(budgets).is_equal_to([30, 60])
    assert_that(warnings).is_length(1)
    assert_that(warnings[0]).contains(
        "timed out",
        "budget 30s",
        "retrying once with 60s",
    )


def test_probe_timing_out_twice_is_skipped_naming_both_budgets(
    monkeypatch: pytest.MonkeyPatch,
    scripted_run: Callable[[list[_Outcome]], list[float]],
) -> None:
    """Two timeouts still fail the probe, and the message names both budgets.

    Args:
        monkeypatch: Pytest fixture for patching modules and attributes.
        scripted_run: Installs scripted ``subprocess.run`` outcomes.
    """
    monkeypatch.setenv("LINTRO_VERSION_TIMEOUT", "30")
    budgets = scripted_run([_timeout(30), _timeout(60)])

    info = check_tool_version("semgrep", ["semgrep"])

    assert_that(info.probe_failed).is_true()
    assert_that(info.current_version).is_none()
    assert_that(info.error_message).starts_with("Failed to run version check: ")
    assert_that(info.error_message).contains(
        "['semgrep', '--version']",
        "timed out after 30 seconds",
        "again after 60 seconds on retry",
    )
    assert_that(budgets).is_equal_to([30, 60])


def test_probe_missing_binary_fails_without_retry(
    monkeypatch: pytest.MonkeyPatch,
    scripted_run: Callable[[list[_Outcome]], list[float]],
    warnings: list[str],
) -> None:
    """A missing binary fails the probe immediately, as before the retry.

    Args:
        monkeypatch: Pytest fixture for patching modules and attributes.
        scripted_run: Installs scripted ``subprocess.run`` outcomes.
        warnings: Captured WARNING-level log messages.
    """
    monkeypatch.setenv("LINTRO_VERSION_TIMEOUT", "30")
    missing = FileNotFoundError(2, "No such file or directory", "semgrep")
    budgets = scripted_run([missing])

    info = check_tool_version("semgrep", ["semgrep"])

    assert_that(info.probe_failed).is_true()
    assert_that(info.current_version).is_none()
    assert_that(info.error_message).is_equal_to(
        f"Failed to run version check: {missing}",
    )
    assert_that(budgets).is_equal_to([30])
    assert_that(warnings).is_empty()


@pytest.mark.parametrize(
    ("first_budget", "retry_budget"),
    [(30, 60), (90, 120), (120, 120), (150, 150)],
    ids=["doubled", "capped_at_120", "at_cap_unchanged", "above_cap_unchanged"],
)
def test_probe_retry_budget_is_doubled_and_capped_at_120(
    first_budget: int,
    retry_budget: int,
    scripted_run: Callable[[list[_Outcome]], list[float]],
) -> None:
    """The retry doubles the budget up to 120s and never shrinks it.

    Args:
        first_budget: Budget of the first attempt.
        retry_budget: Budget the retry is expected to use.
        scripted_run: Installs scripted ``subprocess.run`` outcomes.
    """
    budgets = scripted_run([_timeout(first_budget), "semgrep 1.0.0\n"])

    result = _run_probe_with_retry(_SEMGREP_ARGV, first_budget)

    assert_that(result.stdout).is_equal_to("semgrep 1.0.0\n")
    assert_that(budgets).is_equal_to([first_budget, retry_budget])
