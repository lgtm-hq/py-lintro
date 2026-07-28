"""Tests for the liveness check wired into ``lintro doctor`` (#1614).

Two properties matter here. First, liveness is **opt-in**: under API transport the
probe is a real call, and ``lintro doctor`` must not spend money or reach an
external service on every invocation. Second, when it does run, a depleted balance
or a rejected key must land as a hard doctor status — a soft "unknown" is how an
operator misses that no AI work will run at all.
"""

from __future__ import annotations

from typing import Any

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.ai.config import AIConfig
from lintro.ai.doctor_checks import check_ai_liveness
from lintro.ai.enums import AITransport
from lintro.ai.liveness import LivenessResult, LivenessState
from lintro.cli import cli
from lintro.enums.tool_status import ToolStatus


def _stub_result(
    *,
    state: LivenessState,
    quota_verified: bool = True,
) -> LivenessResult:
    """Build a canned liveness result.

    Args:
        state: The state the stubbed probe should report.
        quota_verified: Whether the probe claims a quota verdict.

    Returns:
        The canned result.
    """
    return LivenessResult(
        provider="anthropic",
        transport=AITransport.API,
        state=state,
        message=f"stubbed {state.value}",
        hint="do the thing",
        quota_verified=quota_verified,
    )


def test_liveness_is_skipped_when_ai_is_disabled() -> None:
    """No AI feature enabled means no probe, so no cost and no noise."""
    assert_that(check_ai_liveness(AIConfig(enabled=False))).is_empty()


def test_liveness_is_skipped_without_a_transport() -> None:
    """Transport resolution is a prerequisite; doctor reports that separately."""
    assert_that(check_ai_liveness(AIConfig(enabled=True))).is_empty()


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (LivenessState.OK, ToolStatus.OK),
        (LivenessState.MISSING_CREDENTIAL, ToolStatus.MISSING),
        (LivenessState.AUTH_FAILED, ToolStatus.INCOMPATIBLE),
        (LivenessState.NO_QUOTA, ToolStatus.INCOMPATIBLE),
        (LivenessState.INCOMPATIBLE_CLI, ToolStatus.INCOMPATIBLE),
        (LivenessState.RATE_LIMITED, ToolStatus.UNKNOWN),
        (LivenessState.UNREACHABLE, ToolStatus.UNKNOWN),
        (LivenessState.UNKNOWN, ToolStatus.UNKNOWN),
    ],
)
def test_liveness_states_map_to_doctor_statuses(
    *,
    state: LivenessState,
    expected: ToolStatus,
    monkeypatch: Any,
) -> None:
    """Every state resolves to a status, with real failures reported as failures.

    Args:
        state: The liveness state the probe reports.
        expected: The doctor status it must map to.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(
        "lintro.ai.doctor_checks.check_liveness_sync",
        lambda *, config: _stub_result(state=state),
    )

    results = check_ai_liveness(
        AIConfig(enabled=True, transport=AITransport.API),
    )

    assert_that(results).is_length(1)
    assert_that(results[0].status).is_equal_to(expected)
    assert_that(results[0].name).is_equal_to("ai.liveness.anthropic")
    assert_that(results[0].hint).is_equal_to("do the thing")


def test_doctor_does_not_probe_without_the_flag(monkeypatch: Any) -> None:
    """The default doctor run must never make a provider call.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    probed = False

    def _probe(*, config: AIConfig) -> LivenessResult:
        nonlocal probed
        probed = True
        del config
        return _stub_result(state=LivenessState.OK)

    monkeypatch.setattr("lintro.ai.doctor_checks.check_liveness_sync", _probe)

    CliRunner().invoke(cli, ["doctor", "--tools", "ruff"])

    assert_that(probed).is_false()


def test_doctor_exposes_the_liveness_flag() -> None:
    """The opt-in must be discoverable, and say that it costs a real call."""
    result = CliRunner().invoke(cli, ["doctor", "--help"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("--ai-liveness")
