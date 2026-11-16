"""A refused agent-CLI batch shim is provider-unavailable everywhere (#3494).

The refusal is raised by the real guard and then read the way ``lintro review
--output json``, the chunk wrapper and the MCP review tool read it: it must
classify as ``provider_unavailable`` and keep the message that carries the
reason and the remedy, not the bare ``UnsafeCommandError`` it chains.
"""

from __future__ import annotations

from typing import Any

import pytest
from assertpy import assert_that

from lintro.ai.exceptions import AIBatchShimRefusedError
from lintro.ai.providers.batch_shim import refuse_unsafe_batch_argv
from lintro.ai.review.error_contract import build_error_contract
from lintro.ai.review.session import aborted_before_completion
from lintro.mcp.errors import McpErrorCode
from lintro.mcp.toolkits import review as mcp_review
from tests.unit.ai.conftest import MockAIProvider

_REMEDY = "instead of a .cmd/.bat shim"


def _refusal(cmd: list[str]) -> AIBatchShimRefusedError:
    """Run the real guard on *cmd* and return the refusal it raises."""
    with pytest.raises(AIBatchShimRefusedError) as excinfo:
        refuse_unsafe_batch_argv(cmd, binary_name="Claude")
    return excinfo.value


_SCHEMA_ARGV = ["C:/npm/claude.cmd", "--json-schema", '{"type":"object"}']
_PATH_ARGV = ["C:/R&D/npm/claude.cmd", "--help"]


@pytest.mark.parametrize(
    ("cmd", "reason"),
    [
        pytest.param(_SCHEMA_ARGV, "the value of --json-schema", id="schema-flag"),
        pytest.param(_PATH_ARGV, "whose path holds", id="metacharacter-path"),
    ],
)
def test_error_contract_reads_a_refused_shim_as_provider_unavailable(
    cmd: list[str],
    reason: str,
) -> None:
    """``build_error_contract`` keeps the kind and the remedy of the refusal."""
    body = build_error_contract(provider="anthropic", error=_refusal(cmd))["error"]

    assert_that(body["kind"]).is_equal_to("provider_unavailable")
    assert_that(body["provider_unavailable"]).is_true()
    assert_that(body["retryable"]).is_false()
    assert_that(body["message"]).contains(reason, _REMEDY)
    assert_that(body["message"]).is_not_equal_to(
        "Unsafe character detected in command name",
    )


def _wrapped(cmd: list[str]) -> Any:
    """Wrap the refusal the way a failing review chunk is wrapped."""
    return aborted_before_completion(
        cause=_refusal(cmd),
        provider=MockAIProvider(),
        chunk_index=0,
        total_chunks=2,
        step="reviewing",
        completed_chunks=0,
    )


def test_review_chunk_wrapper_keeps_the_refusal_kind_and_remedy() -> None:
    """A chunk aborted by the refusal reports it, not invalid_response."""
    wrapper = _wrapped(_PATH_ARGV)

    body = build_error_contract(provider="anthropic", error=wrapper)["error"]

    assert_that(body["kind"]).is_equal_to("provider_unavailable")
    assert_that(body["message"]).contains("whose path holds", _REMEDY)
    assert_that(body["completed_chunks"]).is_equal_to(0)


def test_mcp_review_reports_a_refused_shim_as_tool_unavailable() -> None:
    """MCP answers TOOL_UNAVAILABLE with the remedy, not EXECUTION_ERROR."""
    envelope = mcp_review._review_failure(
        provider_name="anthropic",
        error=_wrapped(_SCHEMA_ARGV),
    )

    assert_that(envelope.code).is_equal_to(McpErrorCode.TOOL_UNAVAILABLE)
    assert_that(envelope.message).contains("--json-schema", _REMEDY)
