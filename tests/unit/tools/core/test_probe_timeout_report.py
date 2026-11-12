"""Tests for reporting a timed-out version probe as ``probe_timeout`` (#2337)."""

from __future__ import annotations

import subprocess  # nosec B404 - only TimeoutExpired/CompletedProcess are used
from collections.abc import Callable, Sequence
from types import SimpleNamespace
from typing import Any

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.enums.probe_failure import ProbeFailure
from lintro.models.core.tool_outcome import ToolOutcome, Unavailable
from lintro.models.core.tool_result import ToolResult
from lintro.tools.core import version_parsing
from lintro.tools.core.snapshots import (
    ToolSnapshot,
    probe_tool,
    snapshot_to_unavailable_result,
)
from lintro.utils.json_output import serialize_tool_result

#: One scripted outcome per probe attempt: an exception to raise, or stdout.
_Outcome = BaseException | str


@pytest.fixture(autouse=True)
def _probe_working_directory(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Probe from a temporary directory; conftest isolates probe caches.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest fixture for patching modules and attributes.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LINTRO_VERSION_TIMEOUT", "30")


@pytest.fixture
def clocked_run(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[list[_Outcome]], None]:
    """Script ``subprocess.run`` so each timed-out attempt uses its full budget.

    The probe module's clock advances by the attempt's budget whenever an
    attempt times out, so the measured probe time is deterministic.

    Args:
        monkeypatch: Pytest fixture for patching modules and attributes.

    Returns:
        Callable[[list[_Outcome]], None]: Installs the scripted outcomes.
    """
    clock = [1000.0]
    monkeypatch.setattr(
        version_parsing,
        "time",
        SimpleNamespace(monotonic=lambda: clock[0]),
    )

    def install(outcomes: list[_Outcome]) -> None:
        pending = list(outcomes)

        def fake_run(
            cmd: Sequence[str],
            **kwargs: Any,
        ) -> subprocess.CompletedProcess[str]:
            outcome = pending.pop(0)
            if isinstance(outcome, subprocess.TimeoutExpired):
                clock[0] += float(kwargs["timeout"])
            if isinstance(outcome, BaseException):
                raise outcome
            return subprocess.CompletedProcess(list(cmd), 0, outcome, "")

        monkeypatch.setattr(subprocess, "run", fake_run)

    return install


def _timeout(seconds: float) -> subprocess.TimeoutExpired:
    """Build the exception ``subprocess.run`` raises for a slow probe.

    Args:
        seconds: Budget the probe exceeded.

    Returns:
        subprocess.TimeoutExpired: Exception for the ruff probe.
    """
    return subprocess.TimeoutExpired(["ruff", "--version"], seconds)


def test_double_timeout_reports_probe_timeout_with_probe_seconds_in_json(
    clocked_run: Callable[[list[_Outcome]], None],
) -> None:
    """Both attempts timing out is a probe timeout, not a missing binary.

    Args:
        clocked_run: Installs scripted ``subprocess.run`` outcomes.
    """
    clocked_run([_timeout(30), _timeout(60)])

    snapshot = probe_tool("ruff")
    report = serialize_tool_result(
        snapshot_to_unavailable_result(snapshot, strict=False),
        action=Action.CHECK,
    )

    assert_that(snapshot.available).is_false()
    assert_that(snapshot.probe_failure).is_equal_to(ProbeFailure.PROBE_TIMEOUT)
    assert_that(report).contains_entry(
        {"status": "unavailable"},
        {"skipped": False},
        {"probe_failure": "probe_timeout"},
        {"probe_seconds": 90.0},
        {"unavailable_reason": "version probe timed out twice (30 s, 60 s)"},
    )
    assert_that(report["output"]).does_not_contain("not found in PATH")


def test_missing_executable_reports_binary_missing_without_probe_seconds(
    clocked_run: Callable[[list[_Outcome]], None],
) -> None:
    """A probe that cannot find its executable stays ``binary_missing``.

    Args:
        clocked_run: Installs scripted ``subprocess.run`` outcomes.
    """
    clocked_run([FileNotFoundError(2, "No such file or directory", "ruff")])

    report = serialize_tool_result(
        snapshot_to_unavailable_result(probe_tool("ruff"), strict=True),
        action=Action.CHECK,
    )

    assert_that(report).contains_entry(
        {"status": "unavailable"},
        {"success": False},
        {"probe_failure": "binary_missing"},
    )
    assert_that(report).does_not_contain_key("probe_seconds")
    assert_that(report["unavailable_reason"]).starts_with(
        "Failed to run version check: ",
    )


def test_single_timeout_then_version_leaves_tool_available(
    clocked_run: Callable[[list[_Outcome]], None],
) -> None:
    """A probe that answers on the retry yields a version and no failure cause.

    Args:
        clocked_run: Installs scripted ``subprocess.run`` outcomes.
    """
    ruff_min = version_parsing.get_minimum_versions()["ruff"]
    clocked_run([_timeout(30), f"ruff {ruff_min}\n"])

    snapshot = probe_tool("ruff")

    assert_that(snapshot.available).is_true()
    assert_that(snapshot.version).is_equal_to(ruff_min)
    assert_that(snapshot.probe_failure).is_none()
    assert_that(snapshot.probe_seconds).is_none()


def test_other_launch_error_has_no_probe_failure_cause(
    clocked_run: Callable[[list[_Outcome]], None],
) -> None:
    """A launch error that is neither a timeout nor a missing file has no code.

    Args:
        clocked_run: Installs scripted ``subprocess.run`` outcomes.
    """
    clocked_run([PermissionError(13, "Permission denied", "ruff")])

    report = serialize_tool_result(
        snapshot_to_unavailable_result(probe_tool("ruff"), strict=False),
        action=Action.CHECK,
    )

    assert_that(report["status"]).is_equal_to("unavailable")
    assert_that(report).does_not_contain_key("probe_failure", "probe_seconds")


def test_snapshot_cache_round_trip_keeps_probe_timeout_evidence() -> None:
    """The snapshot cache preserves the probe cause and timing."""
    snapshot = ToolSnapshot(
        name="semgrep",
        probe_error="version probe timed out twice (30 s, 60 s)",
        command=("semgrep",),
        probe_failure=ProbeFailure.PROBE_TIMEOUT,
        probe_seconds=90.5,
    )

    restored = ToolSnapshot.from_dict(snapshot.to_dict())

    assert_that(snapshot.to_dict()).contains_entry(
        {"probe_failure": "probe_timeout"},
        {"probe_seconds": 90.5},
    )
    assert_that(restored).is_equal_to(snapshot)


@pytest.mark.parametrize(
    ("field", "value"),
    [("probe_failure", "slow"), ("probe_seconds", -1), ("probe_seconds", True)],
    ids=["unknown_cause", "negative_seconds", "boolean_seconds"],
)
def test_snapshot_cache_rejects_malformed_probe_evidence(
    field: str,
    value: object,
) -> None:
    """A corrupt cache entry is rejected rather than reported.

    Args:
        field: Cache key to corrupt.
        value: Malformed value written under that key.
    """
    entry = ToolSnapshot(name="semgrep", command=("semgrep",)).to_dict()
    entry[field] = value

    assert_that(ToolSnapshot.from_dict).raises(ValueError).when_called_with(entry)


def test_tool_result_round_trips_probe_timeout_through_its_outcome() -> None:
    """ToolResult's legacy fields rebuild the same Unavailable outcome."""
    execution = Unavailable(
        reason="version probe timed out twice (30 s, 60 s)",
        probe_failure=ProbeFailure.PROBE_TIMEOUT,
        probe_seconds=90.0,
    )

    result = ToolResult.from_outcome(ToolOutcome(execution=execution), name="x")

    assert_that(result.probe_failure).is_equal_to(ProbeFailure.PROBE_TIMEOUT)
    assert_that(result.probe_seconds).is_equal_to(90.0)
    assert_that(result.outcome.execution).is_equal_to(execution)


def test_probe_seconds_requires_a_probe_timeout() -> None:
    """Probe timing is evidence of a timeout and cannot ride on another cause."""
    assert_that(Unavailable).raises(ValueError).when_called_with(
        reason="Executable not found in PATH",
        probe_failure=ProbeFailure.BINARY_MISSING,
        probe_seconds=1.0,
    )


def test_probe_failure_requires_an_unavailable_result() -> None:
    """A tool that ran cannot carry a probe failure cause."""
    assert_that(ToolResult).raises(ValueError).when_called_with(
        name="x",
        success=True,
        probe_failure=ProbeFailure.PROBE_TIMEOUT,
    )
