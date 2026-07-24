"""End-to-end flag gating for each CLI provider (#1612).

Exercises the guard through the real providers: optional flags are only sent to
binaries that advertise them, and a binary that rejects one anyway triggers a
retry rather than a failed review.
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 - CompletedProcess objects are constructed to drive the providers under test
from collections.abc import Callable, Iterator
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.ai.enums import AITransport
from lintro.ai.json_response import CliSchemaRequest
from lintro.ai.providers.anthropic import AnthropicProvider
from lintro.ai.providers.cursor import CursorProvider
from lintro.ai.providers.openai import OpenAIProvider

_CLAUDE_COMPLETION = json.dumps(
    {
        "result": '{"summary": "ok"}',
        "session_id": "sess-123",
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "total_cost_usd": 0.01,
    },
)
_CURSOR_COMPLETION = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "ok",
        "session_id": "sess-123",
        "usage": {"inputTokens": 10, "outputTokens": 5},
    },
)
_CODEX_COMPLETION = json.dumps(
    {
        "type": "item.completed",
        "item": {"type": "agent_message", "text": "ok"},
    },
)

_SCHEMA = CliSchemaRequest(schema={"type": "object"}, schema_name="lintro_review")


def _runner(
    *,
    help_text: str,
    completion: str,
    version: str,
    reject: str | None = None,
    calls: list[list[str]],
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Build a ``subprocess.run`` stand-in for a guarded CLI provider.

    Args:
        help_text: Text returned for the ``--help`` capability probe.
        completion: Stdout returned for a successful completion call.
        version: Text returned for the ``--version`` probe.
        reject: Optional flag the fake binary rejects with ``unknown option``.
        calls: Sink recording every argv the provider invoked.

    Returns:
        A callable suitable for ``patch("subprocess.run", side_effect=...)``.
    """

    def _run(
        cmd: list[str],
        *args: object,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        if "--version" in cmd:
            return subprocess.CompletedProcess(cmd, 0, version, "")
        if "--help" in cmd:
            return subprocess.CompletedProcess(cmd, 0, help_text, "")
        if reject is not None and reject in cmd:
            return subprocess.CompletedProcess(
                cmd,
                1,
                "",
                f"error: unknown option '{reject}'",
            )
        return subprocess.CompletedProcess(cmd, 0, completion, "")

    return _run


def _completion_calls(calls: list[list[str]]) -> list[list[str]]:
    """Filter capability probes out of the recorded argv lists.

    Args:
        calls: Every argv recorded by the fake runner.

    Returns:
        Only the argv lists of real completion invocations.
    """
    return [cmd for cmd in calls if "--version" not in cmd and "--help" not in cmd]


@pytest.fixture()
def _claude_on_path() -> Iterator[None]:
    """Report the ``claude`` binary as installed.

    Yields:
        None: For the duration of the patched lookup.
    """
    with patch(
        "lintro.ai.providers.anthropic._find_claude",
        return_value="/usr/local/bin/claude",
    ):
        yield


@pytest.fixture()
def _agent_on_path() -> Iterator[None]:
    """Report the Cursor ``agent`` binary as installed.

    Yields:
        None: For the duration of the patched lookup.
    """
    with patch(
        "lintro.ai.providers.cursor._find_agent",
        return_value="/usr/local/bin/agent",
    ):
        yield


@pytest.fixture()
def _codex_on_path() -> Iterator[None]:
    """Report the ``codex`` binary as installed.

    Yields:
        None: For the duration of the patched lookup.
    """
    with patch(
        "lintro.ai.providers.openai._find_codex",
        return_value="/usr/local/bin/codex",
    ):
        yield


# -- Anthropic --------------------------------------------------------------


def test_claude_sends_schema_name_when_advertised(_claude_on_path: None) -> None:
    """Send --json-schema-name to a binary whose help advertises it."""
    calls: list[list[str]] = []
    runner = _runner(
        help_text="  --json-schema <schema>\n  --json-schema-name <name>\n",
        completion=_CLAUDE_COMPLETION,
        version="2.1.218 (Claude Code)",
        calls=calls,
    )
    provider = AnthropicProvider(transport=AITransport.CLI)
    with patch("subprocess.run", side_effect=runner):
        provider.complete("Review this diff", cli_schema=_SCHEMA)

    cmd = _completion_calls(calls)[-1]
    assert_that(cmd).contains("--json-schema-name", "lintro_review")


def test_claude_omits_schema_name_when_not_advertised(_claude_on_path: None) -> None:
    """Keep --json-schema but omit --json-schema-name on a current claude.

    Regression for #1611: ``@anthropic-ai/claude-code`` 2.1.218 removed the
    option and errors out on the whole call when it is sent.
    """
    calls: list[list[str]] = []
    runner = _runner(
        help_text="  --json-schema <schema>  JSON Schema for structured output\n",
        completion=_CLAUDE_COMPLETION,
        version="2.1.218 (Claude Code)",
        calls=calls,
    )
    provider = AnthropicProvider(transport=AITransport.CLI)
    with patch("subprocess.run", side_effect=runner):
        provider.complete("Review this diff", cli_schema=_SCHEMA)

    cmd = _completion_calls(calls)[-1]
    assert_that(cmd).does_not_contain("--json-schema-name")
    assert_that(cmd).contains("--json-schema")


def test_claude_backstop_retries_without_schema_name(_claude_on_path: None) -> None:
    """Retry without --json-schema-name when help lied about supporting it."""
    calls: list[list[str]] = []
    runner = _runner(
        help_text="  --json-schema <schema>\n  --json-schema-name <name>\n",
        completion=_CLAUDE_COMPLETION,
        version="2.1.218 (Claude Code)",
        reject="--json-schema-name",
        calls=calls,
    )
    provider = AnthropicProvider(transport=AITransport.CLI)
    with patch("subprocess.run", side_effect=runner):
        response = provider.complete("Review this diff", cli_schema=_SCHEMA)

    completions = _completion_calls(calls)
    assert_that(completions).is_length(2)
    assert_that(completions[-1]).does_not_contain("--json-schema-name")
    assert_that(response.content).contains("summary")


def test_claude_below_version_floor_raises(_claude_on_path: None) -> None:
    """Refuse a claude binary older than the declared floor."""
    from lintro.ai.exceptions import AINotAvailableError

    calls: list[list[str]] = []
    runner = _runner(
        help_text="  --json-schema <schema>\n",
        completion=_CLAUDE_COMPLETION,
        version="1.0.88 (Claude Code)",
        calls=calls,
    )
    provider = AnthropicProvider(transport=AITransport.CLI)
    with (
        patch("subprocess.run", side_effect=runner),
        pytest.raises(AINotAvailableError, match="1.0.88"),
    ):
        provider.complete("Review this diff")


def test_claude_durable_session_hooks_reset_resume(_claude_on_path: None) -> None:
    """Resume within a durable session and drop the id when it ends."""
    calls: list[list[str]] = []
    runner = _runner(
        help_text="  --resume <id>\n",
        completion=_CLAUDE_COMPLETION,
        version="2.1.218 (Claude Code)",
        calls=calls,
    )
    provider = AnthropicProvider(transport=AITransport.CLI)
    with patch("subprocess.run", side_effect=runner):
        provider.begin_durable_session(repo_root="/tmp/repo")
        provider.complete("first")
        provider.complete("second")
        second = _completion_calls(calls)[1]
        assert_that(second).contains("--resume", "sess-123")

        provider.end_durable_session()
        provider.complete("third")
        third = _completion_calls(calls)[2]
        assert_that(third).does_not_contain("--resume")


# -- Cursor -----------------------------------------------------------------


def test_cursor_omits_trust_when_not_advertised(_agent_on_path: None) -> None:
    """Drop --trust when the installed agent CLI does not advertise it."""
    calls: list[list[str]] = []
    runner = _runner(
        help_text="  --print\n  --output-format <fmt>\n",
        completion=_CURSOR_COMPLETION,
        version="2026.07.09-a3815c0",
        calls=calls,
    )
    provider = CursorProvider(cursor_trust_workspace=True)
    with patch("subprocess.run", side_effect=runner):
        provider.complete("Hello", repo_root="/tmp/repo")

    cmd = _completion_calls(calls)[-1]
    assert_that(cmd).does_not_contain("--trust")
    assert_that(cmd).contains("--workspace", "/tmp/repo")


def test_cursor_backstop_retries_without_resume(_agent_on_path: None) -> None:
    """Retry without --resume when the agent CLI rejects it."""
    calls: list[list[str]] = []
    runner = _runner(
        help_text="  --resume [chatId]\n  --trust\n",
        completion=_CURSOR_COMPLETION,
        version="2026.07.09-a3815c0",
        reject="--resume",
        calls=calls,
    )
    provider = CursorProvider()
    with patch("subprocess.run", side_effect=runner):
        provider.begin_durable_session(repo_root="/tmp/repo")
        provider.complete("first", repo_root="/tmp/repo")
        provider.complete("second", repo_root="/tmp/repo")

    completions = _completion_calls(calls)
    # first (no session yet) + second's --resume attempt + its retry = 3.
    assert_that(completions).is_length(3)
    assert_that(completions[1]).contains("--resume", "sess-123")
    assert_that(completions[-1]).does_not_contain("--resume")


# -- Codex ------------------------------------------------------------------


def test_codex_sends_output_schema_when_advertised(_codex_on_path: None) -> None:
    """Send --output-schema to a codex binary that advertises it."""
    calls: list[list[str]] = []
    runner = _runner(
        help_text="  --json\n  --sandbox <mode>\n  --output-schema <file>\n",
        completion=_CODEX_COMPLETION,
        version="codex-cli 0.60.0",
        calls=calls,
    )
    provider = OpenAIProvider(transport=AITransport.CLI)
    with patch("subprocess.run", side_effect=runner):
        provider.complete("hello", repo_root="/tmp/repo", cli_schema=_SCHEMA)

    cmd = _completion_calls(calls)[-1]
    assert_that(cmd).contains("--output-schema")
    # The prompt stays the trailing positional even after optional flags.
    assert_that(cmd[-1]).is_equal_to("hello")


def test_codex_omits_output_schema_when_not_advertised(_codex_on_path: None) -> None:
    """Fall back to prose parsing when codex has no --output-schema."""
    calls: list[list[str]] = []
    runner = _runner(
        help_text="  --json\n  --sandbox <mode>\n",
        completion=_CODEX_COMPLETION,
        version="codex-cli 0.60.0",
        calls=calls,
    )
    provider = OpenAIProvider(transport=AITransport.CLI)
    with patch("subprocess.run", side_effect=runner):
        provider.complete("hello", repo_root="/tmp/repo", cli_schema=_SCHEMA)

    cmd = _completion_calls(calls)[-1]
    assert_that(cmd).does_not_contain("--output-schema")
    assert_that(cmd[-1]).is_equal_to("hello")


def test_codex_backstop_retries_without_output_schema(_codex_on_path: None) -> None:
    """Retry without --output-schema when codex rejects it."""
    calls: list[list[str]] = []
    runner = _runner(
        help_text="  --json\n  --output-schema <file>\n",
        completion=_CODEX_COMPLETION,
        version="codex-cli 0.60.0",
        reject="--output-schema",
        calls=calls,
    )
    provider = OpenAIProvider(transport=AITransport.CLI)
    with patch("subprocess.run", side_effect=runner):
        response = provider.complete(
            "hello",
            repo_root="/tmp/repo",
            cli_schema=_SCHEMA,
        )

    completions = _completion_calls(calls)
    assert_that(completions).is_length(2)
    assert_that(completions[-1]).does_not_contain("--output-schema")
    assert_that(completions[-1][-1]).is_equal_to("hello")
    assert_that(response.content).is_equal_to("ok")
