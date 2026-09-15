"""Tests for the CLI parallelism clamp and per-call timing (lintro-ops #37)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.providers.response import AIResponse
from lintro.ai.review.merge import ChunkReviewPartial
from lintro.ai.review.models.chunk_timing import ChunkTiming
from lintro.ai.review.run_planning import (
    CLI_DEFAULT_MAX_PARALLEL_CALLS,
    resolve_max_parallel_calls,
)
from lintro.ai.review.timings import ReviewTimingRecorder

# --- parallelism ---------------------------------------------------------------


def _config(**overrides: Any) -> AIConfig:
    """Build an AI config for the clamp tests."""
    fields: dict[str, Any] = {"enabled": True, "review": True}
    fields.update(overrides)
    return AIConfig(**fields)


def test_cli_transport_defaults_to_three_parallel_calls() -> None:
    """Leaving max_parallel_calls unset on the CLI transport clamps to 3."""
    ceiling = resolve_max_parallel_calls(
        ai_config=_config(transport=AITransport.CLI),
        enforce_cost_cap=False,
    )

    assert_that(CLI_DEFAULT_MAX_PARALLEL_CALLS).is_equal_to(3)
    assert_that(ceiling).is_equal_to(3)


def test_cli_transport_honours_an_explicit_max_parallel_calls() -> None:
    """A user who set the field gets their number, even above the clamp."""
    ceiling = resolve_max_parallel_calls(
        ai_config=_config(transport=AITransport.CLI, max_parallel_calls=5),
        enforce_cost_cap=False,
    )

    assert_that(ceiling).is_equal_to(5)


def test_api_transport_keeps_the_config_default() -> None:
    """The API transport is not clamped: five stays five."""
    ceiling = resolve_max_parallel_calls(
        ai_config=_config(transport=AITransport.API),
        enforce_cost_cap=False,
    )

    assert_that(ceiling).is_equal_to(5)


def test_cost_cap_still_serializes_on_every_transport() -> None:
    """An enforced cost cap wins over both the default and the clamp."""
    for transport in (AITransport.API, AITransport.CLI):
        ceiling = resolve_max_parallel_calls(
            ai_config=_config(transport=transport, max_cost_usd=1.0),
            enforce_cost_cap=True,
        )
        assert_that(ceiling).is_equal_to(1)


def test_explicit_value_below_the_clamp_is_kept_on_cli() -> None:
    """The clamp never raises a smaller explicit value."""
    ceiling = resolve_max_parallel_calls(
        ai_config=_config(transport=AITransport.CLI, max_parallel_calls=2),
        enforce_cost_cap=False,
    )

    assert_that(ceiling).is_equal_to(2)


# --- per-call timing -------------------------------------------------------------


def test_chunk_timing_serializes_provider_seconds_and_turns() -> None:
    """The JSON chunk row carries the call's wall time and turn count."""
    timing = ChunkTiming(
        chunk_index=2,
        files=3,
        queued_seconds=0.5,
        in_flight_seconds=12.25,
        provider_seconds=11.0,
        turns=4,
    )

    payload = timing.to_dict()

    assert_that(payload["provider_seconds"]).is_equal_to(11.0)
    assert_that(payload["turns"]).is_equal_to(4)
    assert_that(json.dumps(payload)).contains('"turns": 4')


def test_chunk_timing_reports_unknown_turns_as_null() -> None:
    """A transport that counts no turns yields an explicit ``null``."""
    payload = ChunkTiming(
        chunk_index=0,
        files=1,
        queued_seconds=0.0,
        in_flight_seconds=1.0,
    ).to_dict()

    assert_that(payload).contains_key("turns")
    assert_that(payload["turns"]).is_none()
    assert_that(payload["provider_seconds"]).is_equal_to(0.0)


def test_recorder_clamps_negative_provider_seconds() -> None:
    """A clock that went backwards records zero, not a negative span."""
    recorder = ReviewTimingRecorder()
    recorder.add_chunk(
        chunk_index=0,
        files=1,
        queued_seconds=0.0,
        in_flight_seconds=1.0,
        provider_seconds=-0.2,
        turns=None,
    )

    (chunk,) = recorder.build().chunks

    assert_that(chunk.provider_seconds).is_equal_to(0.0)
    assert_that(chunk.turns).is_none()


def test_chunk_partial_defaults_to_no_call_detail() -> None:
    """A partial built without call detail reports zero seconds, no turns."""
    partial = ChunkReviewPartial(
        summary="",
        checklist=(),
        findings=(),
        input_tokens=0,
        output_tokens=0,
        cost_estimate=0.0,
    )

    assert_that(partial.provider_seconds).is_equal_to(0.0)
    assert_that(partial.turns).is_none()


def test_ai_response_turns_default_to_none() -> None:
    """Providers that do not count turns leave the field unset."""
    assert_that(AIResponse(content="x", model="m").turns).is_none()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (3, 3),
        (0, 0),
        (None, None),
        (True, None),
        (-1, None),
        ("3", None),
    ],
)
def test_claude_cli_envelope_turns(raw: object, expected: int | None) -> None:
    """``num_turns`` reaches ``AIResponse.turns`` only as a non-negative int."""
    from lintro.ai.providers.anthropic.provider import _AnthropicCliTransport

    transport = _AnthropicCliTransport.__new__(_AnthropicCliTransport)
    transport._model = "claude-test"
    envelope: dict[str, Any] = {
        "result": "hello",
        "usage": {"input_tokens": 1, "output_tokens": 1},
        "total_cost_usd": 0.0,
    }
    if raw is not None:
        envelope["num_turns"] = raw

    response, _session = transport.parse_stdout(json.dumps(envelope))

    assert_that(response.turns).is_equal_to(expected)
