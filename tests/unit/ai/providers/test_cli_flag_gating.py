"""End-to-end flag gating for each CLI provider (#1612).

Exercises the guard through the real providers: optional flags are only sent to
binaries that advertise them, and a binary that rejects one anyway triggers a
retry rather than a failed review.
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 - CompletedProcess objects are constructed to drive the providers under test
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.ai import cli_bounds
from lintro.ai.cli_bounds import CliCallOptions
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import AIProviderError, AITurnLimitError
from lintro.ai.json_response import CliSchemaRequest
from lintro.ai.providers.anthropic.provider import AnthropicProvider
from lintro.ai.providers.cursor.provider import CursorProvider
from lintro.ai.providers.openai.provider import (
    OpenAIProvider,
    _openai_strict_schema,
)
from tests.unit.ai.conftest import patch_cli_exec

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
    """Build a spawn stand-in for a guarded CLI provider.

    Args:
        help_text: Text returned for the ``--help`` capability probe.
        completion: Stdout returned for a successful completion call.
        version: Text returned for the ``--version`` probe.
        reject: Optional flag the fake binary rejects with ``unknown option``.
        calls: Sink recording every argv the provider invoked.

    Returns:
        A callable suitable for ``patch_cli_exec(side_effect=...)``.
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
        "lintro.ai.providers.anthropic.provider._find_claude",
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
        "lintro.ai.providers.cursor.provider._find_agent",
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
        "lintro.ai.providers.openai.provider._find_codex",
        return_value="/usr/local/bin/codex",
    ):
        yield


# -- Anthropic --------------------------------------------------------------


async def test_claude_sends_schema_name_when_advertised(_claude_on_path: None) -> None:
    """Send --json-schema-name to a binary whose help advertises it."""
    calls: list[list[str]] = []
    runner = _runner(
        help_text="  --json-schema <schema>\n  --json-schema-name <name>\n  --tools <list>\n",
        completion=_CLAUDE_COMPLETION,
        version="2.1.218 (Claude Code)",
        calls=calls,
    )
    provider = AnthropicProvider(transport=AITransport.CLI)
    with patch_cli_exec(side_effect=runner):
        await provider.complete("Review this diff", cli_schema=_SCHEMA)

    cmd = _completion_calls(calls)[-1]
    assert_that(cmd).contains("--json-schema-name", "lintro_review")


async def test_claude_omits_schema_name_when_not_advertised(
    _claude_on_path: None,
) -> None:
    """Keep --json-schema but omit --json-schema-name on a current claude.

    Regression for #1611: ``@anthropic-ai/claude-code`` 2.1.218 removed the
    option and errors out on the whole call when it is sent.
    """
    calls: list[list[str]] = []
    runner = _runner(
        help_text="  --json-schema <schema>  JSON Schema for structured output\n  --tools <list>\n",
        completion=_CLAUDE_COMPLETION,
        version="2.1.218 (Claude Code)",
        calls=calls,
    )
    provider = AnthropicProvider(transport=AITransport.CLI)
    with patch_cli_exec(side_effect=runner):
        await provider.complete("Review this diff", cli_schema=_SCHEMA)

    cmd = _completion_calls(calls)[-1]
    assert_that(cmd).does_not_contain("--json-schema-name")
    assert_that(cmd).contains("--json-schema")


async def test_claude_backstop_retries_without_schema_name(
    _claude_on_path: None,
) -> None:
    """Retry without --json-schema-name when help lied about supporting it."""
    calls: list[list[str]] = []
    runner = _runner(
        help_text="  --json-schema <schema>\n  --json-schema-name <name>\n  --tools <list>\n",
        completion=_CLAUDE_COMPLETION,
        version="2.1.218 (Claude Code)",
        reject="--json-schema-name",
        calls=calls,
    )
    provider = AnthropicProvider(transport=AITransport.CLI)
    with patch_cli_exec(side_effect=runner):
        response = await provider.complete("Review this diff", cli_schema=_SCHEMA)

    completions = _completion_calls(calls)
    assert_that(completions).is_length(2)
    assert_that(completions[-1]).does_not_contain("--json-schema-name")
    assert_that(response.content).contains("summary")


async def test_claude_below_version_floor_raises(_claude_on_path: None) -> None:
    """Refuse a claude binary older than the declared floor."""
    from lintro.ai.exceptions import AINotAvailableError

    calls: list[list[str]] = []
    runner = _runner(
        help_text="  --json-schema <schema>\n  --tools <list>\n",
        completion=_CLAUDE_COMPLETION,
        version="1.0.88 (Claude Code)",
        calls=calls,
    )
    provider = AnthropicProvider(transport=AITransport.CLI)
    with (
        patch_cli_exec(side_effect=runner),
        pytest.raises(AINotAvailableError, match="1.0.88"),
    ):
        await provider.complete("Review this diff")


async def test_claude_durable_session_hooks_reset_resume(_claude_on_path: None) -> None:
    """Resume within a durable session and drop the id when it ends."""
    calls: list[list[str]] = []
    runner = _runner(
        help_text="  --resume <id>\n  --tools <list>\n",
        completion=_CLAUDE_COMPLETION,
        version="2.1.218 (Claude Code)",
        calls=calls,
    )
    provider = AnthropicProvider(transport=AITransport.CLI)
    with patch_cli_exec(side_effect=runner):
        provider.begin_durable_session(repo_root="/tmp/repo")
        await provider.complete("first")
        await provider.complete("second")
        second = _completion_calls(calls)[1]
        assert_that(second).contains("--resume", "sess-123")

        provider.end_durable_session()
        await provider.complete("third")
        third = _completion_calls(calls)[2]
        assert_that(third).does_not_contain("--resume")


# -- Cursor -----------------------------------------------------------------


async def test_cursor_omits_trust_when_not_advertised(_agent_on_path: None) -> None:
    """Drop --trust when the installed agent CLI does not advertise it."""
    calls: list[list[str]] = []
    runner = _runner(
        help_text="  --print\n  --output-format <fmt>\n",
        completion=_CURSOR_COMPLETION,
        version="2026.07.09-a3815c0",
        calls=calls,
    )
    provider = CursorProvider(cursor_trust_workspace=True)
    with patch_cli_exec(side_effect=runner):
        await provider.complete("Hello", repo_root="/tmp/repo")

    cmd = _completion_calls(calls)[-1]
    assert_that(cmd).does_not_contain("--trust")
    assert_that(cmd).contains("--workspace", "/tmp/repo")


async def test_cursor_backstop_retries_without_resume(_agent_on_path: None) -> None:
    """Retry without --resume when the agent CLI rejects it."""
    calls: list[list[str]] = []
    runner = _runner(
        help_text="  --resume [chatId]\n  --trust\n",
        completion=_CURSOR_COMPLETION,
        version="2026.07.09-a3815c0",
        reject="--resume",
        calls=calls,
    )
    provider = CursorProvider(cursor_trust_workspace=True)
    with patch_cli_exec(side_effect=runner):
        provider.begin_durable_session(repo_root="/tmp/repo")
        await provider.complete("first", repo_root="/tmp/repo")
        await provider.complete("second", repo_root="/tmp/repo")

    completions = _completion_calls(calls)
    # first (no session yet) + second's --resume attempt + its retry = 3.
    assert_that(completions).is_length(3)
    assert_that(completions[1]).contains("--resume", "sess-123")
    assert_that(completions[-1]).does_not_contain("--resume")
    # The retry drops --resume only; the explicit trust grant survives it.
    assert_that(completions[-1]).contains("--trust")


# -- Codex ------------------------------------------------------------------


async def test_codex_sends_output_schema_when_advertised(_codex_on_path: None) -> None:
    """Send --output-schema to a codex binary that advertises it."""
    calls: list[list[str]] = []
    runner = _runner(
        help_text="  --json\n  --sandbox <mode>\n  --output-schema <file>\n",
        completion=_CODEX_COMPLETION,
        version="codex-cli 0.60.0",
        calls=calls,
    )
    provider = OpenAIProvider(transport=AITransport.CLI)
    with patch_cli_exec(side_effect=runner):
        await provider.complete("hello", repo_root="/tmp/repo", cli_schema=_SCHEMA)

    cmd = _completion_calls(calls)[-1]
    assert_that(cmd).contains("--output-schema")
    # The prompt stays the trailing positional even after optional flags.
    assert_that(cmd[-1]).is_equal_to("-")


async def test_codex_output_schema_points_at_temp_file_not_inline_json(
    _codex_on_path: None,
) -> None:
    """--output-schema must carry a file PATH containing the schema.

    codex reads the flag's value as a filename: passing the schema JSON itself
    made codex try to open a file named after the whole schema text and abort
    with "Filename too long" (os error 36) before any request was sent
    (first live Codex-lane dogfood, #2472). The file must hold the schema and
    be cleaned up after the call.
    """
    calls: list[list[str]] = []
    runner = _runner(
        help_text="  --json\n  --sandbox <mode>\n  --output-schema <file>\n",
        completion=_CODEX_COMPLETION,
        version="codex-cli 0.60.0",
        calls=calls,
    )
    provider = OpenAIProvider(transport=AITransport.CLI)
    with patch_cli_exec(side_effect=runner):
        await provider.complete("hello", repo_root="/tmp/repo", cli_schema=_SCHEMA)

    cmd = _completion_calls(calls)[-1]
    schema_arg = cmd[cmd.index("--output-schema") + 1]
    assert_that(schema_arg).ends_with(".json")
    assert_that(schema_arg.startswith("{")).is_false()
    # The schema rides in the file, and the temp file is cleaned up.
    assert_that(Path(schema_arg).exists()).is_false()
    # The path was written with the schema when the call was made.
    assert_that(cmd.count("--output-schema")).is_equal_to(1)


async def test_codex_output_schema_is_normalized_for_openai_strict_mode(
    _codex_on_path: None,
) -> None:
    """The written schema must satisfy OpenAI strict structured outputs.

    OpenAI rejects schemas whose ``required`` omits any ``properties`` key
    (``invalid_json_schema``: "Missing 'finding_ref'") — the exact failure of
    the first live Codex-lane dogfood. The temp file must therefore carry the
    normalized form: every property required, formerly-optional ones nullable,
    and the temp file removed after the call.
    """
    schema_with_optional_key = CliSchemaRequest(
        schema={
            "type": "object",
            "required": ["summary"],
            "additionalProperties": False,
            "properties": {
                "summary": {
                    "type": "object",
                    "required": ["headline"],
                    "additionalProperties": False,
                    "properties": {
                        "headline": {"type": "string"},
                        "walkthrough": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["text"],
                                "additionalProperties": False,
                                "properties": {
                                    "text": {"type": "string"},
                                    "finding_ref": {"type": "string"},
                                },
                            },
                        },
                    },
                },
                "flagged_files": {"type": "array"},
            },
        },
        schema_name="lintro_review",
    )
    captured: dict[str, str] = {}
    calls: list[list[str]] = []
    runner = _runner(
        help_text="  --json\n  --sandbox <mode>\n  --output-schema <file>\n",
        completion=_CODEX_COMPLETION,
        version="codex-cli 0.60.0",
        calls=calls,
    )

    def _spy(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """Capture the schema file content at spawn time (pre-cleanup)."""
        if "--output-schema" in cmd:
            path = cmd[cmd.index("--output-schema") + 1]
            captured["schema"] = Path(path).read_text(encoding="utf-8")
        return runner(cmd, **kwargs)

    provider = OpenAIProvider(transport=AITransport.CLI)
    with patch_cli_exec(side_effect=_spy):
        await provider.complete(
            "hello",
            repo_root="/tmp/repo",
            cli_schema=schema_with_optional_key,
        )

    written = json.loads(captured["schema"])
    summary = written["properties"]["summary"]
    # Every property is required; optional ones are nullable instead.
    assert_that(sorted(written["required"])).is_equal_to(
        ["flagged_files", "summary"],
    )
    assert_that(written["properties"]["flagged_files"]["type"]).is_equal_to(
        ["array", "null"],
    )
    assert_that(sorted(summary["required"])).is_equal_to(["headline", "walkthrough"])
    assert_that(summary["properties"]["walkthrough"]["type"]).is_equal_to(
        ["array", "null"],
    )
    bullet = summary["properties"]["walkthrough"]["items"]
    assert_that(sorted(bullet["required"])).is_equal_to(["finding_ref", "text"])
    assert_that(bullet["properties"]["finding_ref"]["type"]).is_equal_to(
        ["string", "null"],
    )
    assert_that(bullet["properties"]["text"]["type"]).is_equal_to("string")
    # Required, non-optional properties keep their original type.
    assert_that(written["properties"]["summary"]["type"]).is_equal_to("object")
    # And the temp file did not survive the call.
    schema_arg = _completion_calls(calls)[-1][
        _completion_calls(calls)[-1].index("--output-schema") + 1
    ]
    assert_that(Path(schema_arg).exists()).is_false()


def test_openai_strict_schema_makes_composite_optional_types_nullable() -> None:
    """Optional list-typed and anyOf/oneOf properties gain a null variant."""
    schema = {
        "type": "object",
        "required": ["kept"],
        "properties": {
            "kept": {"type": ["string", "number"]},
            "multi": {"type": ["string", "number"]},
            "already": {"type": ["string", "null"]},
            "any": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
            "one": {"oneOf": [{"type": "string"}, {"type": "null"}]},
            "untyped": {"description": "no type key"},
        },
    }

    normalized = _openai_strict_schema(schema)

    props = normalized["properties"]
    assert_that(sorted(normalized["required"])).is_equal_to(
        ["already", "any", "kept", "multi", "one", "untyped"],
    )
    assert_that(props["kept"]["type"]).is_equal_to(["string", "number"])
    assert_that(props["multi"]["type"]).is_equal_to(["string", "number", "null"])
    assert_that(props["already"]["type"]).is_equal_to(["string", "null"])
    assert_that(props["any"]["anyOf"]).is_equal_to(
        [{"type": "string"}, {"type": "integer"}, {"type": "null"}],
    )
    assert_that(props["one"]["oneOf"]).is_equal_to(
        [{"type": "string"}, {"type": "null"}],
    )
    assert_that(props["untyped"]).is_equal_to({"description": "no type key"})
    # Input is not mutated.
    assert_that(schema["required"]).is_equal_to(["kept"])


async def test_codex_output_schema_temp_file_cleaned_up_after_retry_ladder(
    _codex_on_path: None,
) -> None:
    """The schema temp file outlives the backstop retry, then is removed.

    The cleanup runs after the guarded call returns (including its retry
    ladder): the first attempt carries the schema file, the retry drops the
    rejected flag, and no temp file is left behind once the call settles.
    """
    calls: list[list[str]] = []
    runner = _runner(
        help_text="  --json\n  --sandbox <mode>\n  --output-schema <file>\n",
        completion=_CODEX_COMPLETION,
        version="codex-cli 0.60.0",
        reject="--output-schema",
        calls=calls,
    )
    provider = OpenAIProvider(transport=AITransport.CLI)
    with patch_cli_exec(side_effect=runner):
        await provider.complete("hello", repo_root="/tmp/repo", cli_schema=_SCHEMA)

    completions = _completion_calls(calls)
    assert_that(completions).is_length(2)
    first_schema_path = completions[0][completions[0].index("--output-schema") + 1]
    assert_that(completions[-1]).does_not_contain("--output-schema")
    assert_that(Path(first_schema_path).exists()).is_false()


async def test_codex_omits_output_schema_when_not_advertised(
    _codex_on_path: None,
) -> None:
    """Fall back to prose parsing when codex has no --output-schema."""
    calls: list[list[str]] = []
    runner = _runner(
        help_text="  --json\n  --sandbox <mode>\n",
        completion=_CODEX_COMPLETION,
        version="codex-cli 0.60.0",
        calls=calls,
    )
    provider = OpenAIProvider(transport=AITransport.CLI)
    with patch_cli_exec(side_effect=runner):
        await provider.complete("hello", repo_root="/tmp/repo", cli_schema=_SCHEMA)

    cmd = _completion_calls(calls)[-1]
    assert_that(cmd).does_not_contain("--output-schema")
    assert_that(cmd[-1]).is_equal_to("-")


async def test_codex_backstop_retries_without_output_schema(
    _codex_on_path: None,
) -> None:
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
    with patch_cli_exec(side_effect=runner):
        response = await provider.complete(
            "hello",
            repo_root="/tmp/repo",
            cli_schema=_SCHEMA,
        )

    completions = _completion_calls(calls)
    assert_that(completions).is_length(2)
    assert_that(completions[-1]).does_not_contain("--output-schema")
    assert_that(completions[-1][-1]).is_equal_to("-")
    assert_that(response.content).is_equal_to("ok")


# -- Anthropic: per-call bounds (#2685) --------------------------------------

_CLAUDE_TURN_LIMITED = json.dumps(
    {
        "type": "result",
        "subtype": "error_max_turns",
        "is_error": True,
        "num_turns": 3,
        "result": "",
        "session_id": "sess-123",
        "usage": {"input_tokens": 10, "output_tokens": 4},
        "total_cost_usd": 0.02,
    },
)


async def test_claude_empties_the_tool_list_on_a_single_shot_call(
    _claude_on_path: None,
) -> None:
    """A call bound with ``tools_disabled`` keeps ``--tools`` and sends it empty (#2731).

    The read-only bound stays a required contract flag; only its value
    changes, so the agent has no tool to spend a turn on.
    """
    token = cli_bounds._CURRENT_CALL.set(
        CliCallOptions(max_turns=12, tools_disabled=True),
    )
    try:
        calls: list[list[str]] = []
        runner = _runner(
            help_text="  --tools <list>\n  --max-turns <n>\n  --json-schema <schema>\n  --json-schema-name <name>\n",
            completion=_CLAUDE_COMPLETION,
            version="2.1.218 (Claude Code)",
            calls=calls,
        )
        provider = AnthropicProvider(transport=AITransport.CLI)
        with patch_cli_exec(side_effect=runner):
            await provider.complete("Review this diff", cli_schema=_SCHEMA)

        cmd = _completion_calls(calls)[-1]
        assert_that(cmd[cmd.index("--tools") + 1]).is_equal_to("")
        assert_that(cmd).does_not_contain("Read,Grep,Glob")
        assert_that(cmd).contains("--max-turns", "12")
    finally:
        cli_bounds._CURRENT_CALL.reset(token)


async def test_claude_bounds_the_call_when_help_advertises_the_flags(
    _claude_on_path: None,
) -> None:
    """Send --tools Read,Grep,Glob and --max-turns to a binary that advertises them (#2685)."""
    token = cli_bounds._CURRENT_CALL.set(CliCallOptions(max_turns=3))
    try:
        calls: list[list[str]] = []
        runner = _runner(
            help_text="  --tools <list>\n  --max-turns <n>\n  --json-schema <schema>\n  --json-schema-name <name>\n",
            completion=_CLAUDE_COMPLETION,
            version="2.1.218 (Claude Code)",
            calls=calls,
        )
        provider = AnthropicProvider(transport=AITransport.CLI)
        with patch_cli_exec(side_effect=runner):
            await provider.complete("Review this diff", cli_schema=_SCHEMA)

        cmd = _completion_calls(calls)[-1]
        cmd = _completion_calls(calls)[-1]
        assert_that(cmd).contains("--tools", "Read,Grep,Glob", "--max-turns", "3")
        assert_that(cmd.index("--max-turns") + 1).is_equal_to(cmd.index("3"))
    finally:
        cli_bounds._CURRENT_CALL.reset(token)


async def test_claude_refuses_a_binary_that_cannot_restrict_tools(
    _claude_on_path: None,
) -> None:
    """Without --tools the read-only bound cannot hold, so the CLI is refused.

    Fail closed (#2685): prompt-injected repository content must never reach a
    writable tool because an older binary happened to be installed.
    """
    from lintro.ai.exceptions import AINotAvailableError

    token = cli_bounds._CURRENT_CALL.set(CliCallOptions(max_turns=3))
    try:
        calls: list[list[str]] = []
        runner = _runner(
            help_text="  --json-schema <schema>\n",
            completion=_CLAUDE_COMPLETION,
            version="2.1.273",
            calls=calls,
        )
        provider = AnthropicProvider(transport=AITransport.CLI)
        with (
            patch_cli_exec(side_effect=runner),
            pytest.raises(AINotAvailableError) as info,
        ):
            await provider.complete("Review this", cli_schema=_SCHEMA)
        assert_that(str(info.value)).contains("--tools", "npm install -g")
        assert_that(_completion_calls(calls)).is_empty()
    finally:
        cli_bounds._CURRENT_CALL.reset(token)


async def test_claude_refuses_when_help_cannot_be_read(
    _claude_on_path: None,
) -> None:
    """An unreadable --help cannot confirm --tools, so the CLI is refused.

    ``supports_flag`` is optimistic on a failed probe, which suits optional
    flags; the read-only bound must not inherit that optimism (#2685).
    """
    from lintro.ai.exceptions import AINotAvailableError

    calls: list[list[str]] = []

    def _run(
        cmd: list[str],
        *args: object,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        if "--version" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "2.1.273", "")
        if "--help" in cmd:
            return subprocess.CompletedProcess(cmd, 1, "", "help crashed")
        return subprocess.CompletedProcess(cmd, 0, _CLAUDE_COMPLETION, "")

    provider = AnthropicProvider(transport=AITransport.CLI)
    with (
        patch_cli_exec(side_effect=_run),
        pytest.raises(AINotAvailableError) as info,
    ):
        await provider.complete("Review this", cli_schema=_SCHEMA)
    assert_that(str(info.value)).contains("could not be read", "npm install -g")
    assert_that(_completion_calls(calls)).is_empty()


async def test_claude_reports_a_turn_limited_envelope_as_a_turn_limit_error(
    _claude_on_path: None,
) -> None:
    """An error_max_turns envelope raises AITurnLimitError, never a parsed answer (#2685)."""
    token = cli_bounds._CURRENT_CALL.set(CliCallOptions(max_turns=3))
    try:
        calls: list[list[str]] = []
        runner = _runner(
            help_text="  --tools <list>\n  --max-turns <n>\n  --json-schema <schema>\n  --json-schema-name <name>\n",
            completion=_CLAUDE_TURN_LIMITED,
            version="2.1.218 (Claude Code)",
            calls=calls,
        )
        provider = AnthropicProvider(transport=AITransport.CLI)
        with patch_cli_exec(side_effect=runner):
            with pytest.raises(AITurnLimitError):
                await provider.complete("Review this diff", cli_schema=_SCHEMA)

        _completion_calls(calls)[-1]
    finally:
        cli_bounds._CURRENT_CALL.reset(token)


async def test_claude_renders_tools_and_no_max_turns_without_bounds(
    _claude_on_path: None,
) -> None:
    """A direct provider call without bounds is read-only but turn-unlimited.

    ``--tools`` rides the base argv of every Claude call; only ``--max-turns``
    comes from the bounds context variable (#2685).
    """
    calls: list[list[str]] = []
    runner = _runner(
        help_text="  --json-schema <schema>\n  --tools <list>\n  --max-turns <n>\n",
        completion=_CLAUDE_COMPLETION,
        version="2.1.273",
        calls=calls,
    )
    assert_that(cli_bounds.current_cli_call_options()).is_none()
    provider = AnthropicProvider(model="claude-sonnet-4-6", transport=AITransport.CLI)
    with patch_cli_exec(side_effect=runner):
        await provider.complete("Review this", cli_schema=_SCHEMA)
    cmd = _completion_calls(calls)[-1]
    # Read-only always; only the turn limit depends on bounds being in force.
    assert_that(cmd).contains("--tools", "Read,Grep,Glob")
    assert_that(cmd).does_not_contain("--max-turns")


async def test_concurrent_claude_calls_each_render_their_own_turn_limit(
    _claude_on_path: None,
) -> None:
    """Task-local bounds: two overlapping calls never see each other's limit."""
    import asyncio

    from lintro.ai.config import AIConfig
    from lintro.ai.invoke import call_ai

    calls: list[list[str]] = []
    runner = _runner(
        help_text="  --json-schema <schema>\n  --tools <list>\n  --max-turns <n>\n",
        completion=_CLAUDE_COMPLETION,
        version="2.1.273",
        calls=calls,
    )
    provider = AnthropicProvider(model="claude-sonnet-4-6", transport=AITransport.CLI)

    def _config(limit: int) -> AIConfig:
        return AIConfig.model_validate(
            {
                "enabled": True,
                "transport": "cli",
                "max_parallel_calls": 2,
                "transports": {"cli": {"max_turns": limit}},
            },
        )

    async def _one(limit: int) -> None:
        await call_ai(
            provider=provider,
            ai_config=_config(limit),
            user_prompt=f"prompt {limit}",
            system_prompt=None,
            budget=None,
            use_one_shot=True,
        )

    with patch_cli_exec(side_effect=runner):
        await asyncio.gather(_one(2), _one(5))

    rendered = sorted(
        cmd[cmd.index("--max-turns") + 1] for cmd in _completion_calls(calls)
    )
    assert_that(rendered).is_equal_to(["2", "5"])
    for cmd in _completion_calls(calls):
        assert_that(cmd).contains("--tools", "Read,Grep,Glob")


async def test_claude_turn_limit_wins_over_the_exit_code_auth_heuristic(
    _claude_on_path: None,
) -> None:
    """A limited run exits 1 and warns about the login on stderr; it is not auth.

    Claude prints "claude.ai connectors are disabled ... your claude.ai login"
    on stderr when another auth source is set, and a turn-limited run exits
    non-zero. Read the envelope first so the run is a turn limit, never an
    authentication failure (#2685).
    """
    token = cli_bounds._CURRENT_CALL.set(CliCallOptions(max_turns=3))
    try:
        calls: list[list[str]] = []

        def _run(cmd: list[str], *args: object, **kwargs: object) -> Any:
            calls.append(list(cmd))
            if "--version" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "2.1.273", "")
            if "--help" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "  --tools <list>\n", "")
            return subprocess.CompletedProcess(
                cmd,
                1,
                _CLAUDE_TURN_LIMITED,
                "claude.ai connectors are disabled because another auth source "
                "takes precedence over your claude.ai login",
            )

        provider = AnthropicProvider(transport=AITransport.CLI)
        with patch_cli_exec(side_effect=_run), pytest.raises(AITurnLimitError) as info:
            await provider.complete("Review this", cli_schema=_SCHEMA)
        assert_that(_completion_calls(calls)[-1]).contains("--max-turns", "3")
        # The stopped call's usage rides on the error for the budget (#2685).
        assert_that(info.value.input_tokens).is_equal_to(10)
        assert_that(info.value.output_tokens).is_equal_to(4)
        assert_that(info.value.cost_estimate).is_equal_to(0.02)
        assert_that(info.value.turns).is_equal_to(3)
    finally:
        cli_bounds._CURRENT_CALL.reset(token)


async def test_claude_does_not_read_a_turn_limit_after_the_backstop_dropped_it(
    _claude_on_path: None,
) -> None:
    """Once --max-turns is stripped, an unrelated error is not a turn limit.

    The count fallback (``is_error`` with ``num_turns`` at the limit) must key
    on the limit the executed argv carried, not the one requested (#2685).
    """
    token = cli_bounds._CURRENT_CALL.set(CliCallOptions(max_turns=3))
    try:
        calls: list[list[str]] = []
        unrelated = json.dumps(
            {"is_error": True, "num_turns": 5, "result": "something else broke"},
        )

        def _run(cmd: list[str], *args: object, **kwargs: object) -> Any:
            calls.append(list(cmd))
            if "--version" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "2.1.273", "")
            if "--help" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "  --tools <list>\n", "")
            if "--max-turns" in cmd:
                return subprocess.CompletedProcess(
                    cmd,
                    1,
                    "",
                    "error: unknown option '--max-turns'",
                )
            return subprocess.CompletedProcess(cmd, 1, unrelated, "")

        provider = AnthropicProvider(transport=AITransport.CLI)
        with patch_cli_exec(side_effect=_run), pytest.raises(AIProviderError) as info:
            await provider.complete("Review this", cli_schema=_SCHEMA)
        assert_that(type(info.value)).is_equal_to(AIProviderError)
        assert_that(_completion_calls(calls)[-1]).does_not_contain("--max-turns")
    finally:
        cli_bounds._CURRENT_CALL.reset(token)
