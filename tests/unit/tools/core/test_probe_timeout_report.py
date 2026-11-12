"""Tests for reporting a timed-out version probe as ``probe_timeout`` (#2337)."""

from __future__ import annotations

import json
import subprocess  # nosec B404 - only TimeoutExpired/CompletedProcess are used
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro import __version__ as lintro_version
from lintro.enums.action import Action
from lintro.enums.probe_failure import ProbeFailure
from lintro.models.core.tool_outcome import ToolOutcome, Unavailable
from lintro.models.core.tool_result import ToolResult
from lintro.tools.core import version_parsing
from lintro.tools.core.snapshots import (
    DEFAULT_SNAPSHOT_TTL_SECONDS,
    ToolSnapshot,
    _snapshot_from_version,
    probe_all_tools,
    probe_tool,
    snapshot_to_unavailable_result,
)
from lintro.tools.core.version_parsing import ToolVersionInfo
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


def test_cached_probe_seconds_without_timeout_is_reprobed_not_crashed(
    tmp_path: Path,
) -> None:
    """A cache entry pairing timing with another cause is discarded on load.

    Args:
        tmp_path: Pytest temporary directory holding the cache.
    """
    mismatched = ToolSnapshot(
        name="ruff",
        probe_error="stale cached evidence",
        command=("ruff",),
        probe_failure=ProbeFailure.BINARY_MISSING,
        probe_seconds=90.0,
        probed_at=time.time(),
    )
    cache_file = tmp_path / ".lintro-cache" / "tool-snapshots.json"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text(
        json.dumps(
            {
                "lintro_version": lintro_version,
                "probed_at": time.time(),
                "ttl_seconds": DEFAULT_SNAPSHOT_TTL_SECONDS,
                "snapshots": {"ruff": mismatched.to_dict()},
            },
        ),
        encoding="utf-8",
    )
    fresh = replace(
        mismatched,
        probe_error="Failed to run version check: fresh probe",
        probe_seconds=None,
    )

    with (
        patch(
            "lintro.tools.core.snapshots.get_executable_command",
            return_value=["ruff"],
        ),
        patch("lintro.tools.core.snapshots.shutil.which", return_value=None),
        patch("lintro.tools.core.snapshots.probe_tool", return_value=fresh),
        patch("lintro.plugins.discovery.discover_all_tools", return_value=None),
        patch(
            "lintro.plugins.registry.ToolRegistry.get_names",
            return_value=["ruff"],
        ),
    ):
        snapshot = probe_all_tools(cache_root=tmp_path)["ruff"]
    report = serialize_tool_result(
        snapshot_to_unavailable_result(snapshot, strict=False),
        action=Action.CHECK,
    )

    assert_that(report).contains_entry(
        {"unavailable_reason": "Failed to run version check: fresh probe"},
        {"probe_failure": "binary_missing"},
    )
    assert_that(report).does_not_contain_key("probe_seconds")
    assert_that(ToolSnapshot.from_dict).raises(ValueError).when_called_with(
        mismatched.to_dict(),
    )


def test_module_launch_that_exits_nonzero_is_not_binary_missing() -> None:
    """``python -m tool`` failing with no PATH binary has no probe cause."""
    snapshot = ToolSnapshot(
        name="sometool",
        command=("python", "-m", "sometool"),
    )
    info = ToolVersionInfo(
        name="sometool",
        min_version="1.0.0",
        error_message="Command failed: python -m sometool --version",
        probe_failed=True,
    )

    result = _snapshot_from_version(snapshot, info, "Install sometool")

    assert_that(result.available).is_false()
    assert_that(result.probe_failure).is_none()
    assert_that(result.probe_error).contains("Command failed")
