"""Invalid ``ai:`` config values on the review surfaces (#3116).

``lintro review`` and the ``lintro_review`` MCP tool resolve the ``ai:``
section themselves rather than through the run pipeline. A value ``AIConfig``
rejected used to escape both as a raw pydantic ``ValidationError``: a
traceback with exit 1 on the CLI, and an ``execution_error`` envelope carrying
pydantic's multi-line dump on MCP. Both quoted the rejected value, which for
``ai.api_key_env`` can be a key pasted into the wrong field.

The CLI now prints the one ``Error:`` line ``chk``/``fmt`` print and exits 2;
MCP answers with the ``invalid_input`` envelope it already uses for a bad
``LINTRO_AI_*`` override.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that
from click.testing import CliRunner
from mcp.client import Client

from lintro.cli import cli
from lintro.mcp.enums.mcp_error_code import McpErrorCode
from tests.unit.mcp.session_helpers import payload_from_result, run_in_memory_client

_ENABLED = "ai:\n  enabled: true\n  review: true\n  provider: anthropic\n"

#: ``(ai: body appended to an enabled review config, field path the error
#: must name, rejected value that must never be echoed back)``.
_INVALID = {
    "wrong_type": ("  max_tokens: lots\n", "ai.max_tokens", "lots"),
    "secret_shaped_string": (
        '  api_key_env: "sk-ant-NOT-A-REAL-KEY-0123456789"\n',
        "ai.api_key_env",
        "sk-ant-",
    ),
    "nested_provider": (
        "  providers:\n    anthropic:\n      cli_bare: sometimes-maybe\n",
        "ai.providers.anthropic.cli_bare",
        "sometimes-maybe",
    ),
}

#: Text only pydantic's own rendering of an error carries.
_PYDANTIC_DUMP = ("Traceback", "validation error for", "input_value")


@pytest.fixture
def write_config(
    ai_config_project: Callable[[str], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[str | None], Path]:
    """Return a factory for a project whose AI dependencies look installed.

    Args:
        ai_config_project: Writer for an isolated project config.
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        Callable[[str | None], Path]: A factory taking the full ``ai:``
        section (None for none) and returning the project root, already the
        working directory.
    """
    import lintro.ai.availability as availability
    import lintro.cli_utils.commands.review as review_module

    monkeypatch.setattr(availability, "is_ai_available", lambda: True)
    monkeypatch.setattr(review_module, "require_ai", lambda: None)

    def _write(ai_section: str | None) -> Path:
        return ai_config_project(
            "tools:\n  ruff:\n    enabled: true\n" + (ai_section or ""),
        )

    return _write


def _call_mcp_review(workspace: Path) -> tuple[bool, dict[str, Any]]:
    """Call ``lintro_review`` through a real in-memory MCP client session.

    Args:
        workspace: Workspace root the server is anchored to.

    Returns:
        Whether the result is flagged as an error, and its payload.
    """

    async def _check(client: Client) -> tuple[bool, dict[str, Any]]:
        result = await client.call_tool(
            name="lintro_review",
            arguments={"uncommitted": True},
        )
        return bool(result.is_error), payload_from_result(result)

    return run_in_memory_client(workspace=workspace, registry=None, check=_check)


@pytest.mark.parametrize("case", sorted(_INVALID))
@pytest.mark.parametrize(
    "extra_args",
    [[], ["--output", "json"]],
    ids=["text", "json"],
)
def test_review_cli_reports_an_invalid_ai_value_on_one_line(
    write_config: Callable[[str | None], Path],
    case: str,
    extra_args: list[str],
) -> None:
    """``lintro review`` exits 2 naming the field, with no traceback or value."""
    body, field_path, rejected = _INVALID[case]
    write_config(_ENABLED + body)

    result = CliRunner().invoke(cli, ["review", "--uncommitted", *extra_args])

    assert_that(result.exception).described_as(repr(result.exception)).is_instance_of(
        SystemExit,
    )
    assert_that(result.exit_code).is_equal_to(2)
    error_lines = [line for line in result.output.splitlines() if "Error:" in line]
    assert_that(error_lines).is_length(1)
    assert_that(error_lines[0]).contains("invalid AI configuration", field_path)
    assert_that(result.output).does_not_contain(*_PYDANTIC_DUMP, rejected)


@pytest.mark.parametrize("case", sorted(_INVALID))
def test_mcp_review_reports_an_invalid_ai_value_as_invalid_input(
    write_config: Callable[[str | None], Path],
    case: str,
) -> None:
    """The MCP tool answers with its config-error envelope, not a crash dump."""
    body, field_path, rejected = _INVALID[case]
    workspace = write_config(_ENABLED + body)

    is_error, payload = _call_mcp_review(workspace)

    assert_that(is_error).is_true()
    error = payload["error"]
    assert_that(error["code"]).is_equal_to(McpErrorCode.INVALID_INPUT.value)
    assert_that(error["detail"]).is_equal_to(
        {"tool": "lintro_review", "reason": "invalid_ai_config"},
    )
    assert_that(error["message"]).starts_with("invalid AI configuration: ")
    assert_that(error["message"]).contains(field_path)
    assert_that(error["message"]).does_not_contain("\n")
    assert_that(json.dumps(payload)).does_not_contain(*_PYDANTIC_DUMP, rejected)


@pytest.mark.parametrize(
    "ai_section",
    [None, "ai:\n  enabled: true\n  review: false\n  provider: anthropic\n"],
    ids=["no-ai-section", "valid-ai-section"],
)
def test_valid_or_absent_ai_config_still_reaches_the_review_gate(
    write_config: Callable[[str | None], Path],
    ai_section: str | None,
) -> None:
    """Controls: a resolvable config is judged by the review gate as before."""
    workspace = write_config(ai_section)

    cli_result = CliRunner().invoke(cli, ["review", "--uncommitted"])
    is_error, payload = _call_mcp_review(workspace)

    assert_that(cli_result.exit_code).is_equal_to(2)
    assert_that(cli_result.output).contains("AI review is disabled")
    assert_that(cli_result.output).does_not_contain("invalid AI configuration")
    assert_that(is_error).is_true()
    assert_that(payload["error"]["code"]).is_equal_to(
        McpErrorCode.TOOL_UNAVAILABLE.value,
    )
    assert_that(payload["error"]["detail"]["reason"]).is_equal_to("review_disabled")
