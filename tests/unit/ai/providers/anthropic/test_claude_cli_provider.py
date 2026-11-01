"""Tests for Anthropic Claude CLI provider backend."""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.ai.enums import AITransport, CliBareMode
from lintro.ai.exceptions import AIAuthenticationError, AINotAvailableError
from lintro.ai.providers.anthropic.provider import AnthropicProvider, _find_claude
from lintro.ai.registry import AIProvider
from tests.unit.ai.conftest import CLAUDE_HELP, patch_cli_exec


@pytest.fixture()
def _mock_claude_on_path() -> Iterator[None]:
    """Patch claude binary discovery for CLI transport tests."""
    with patch(
        "lintro.ai.providers.anthropic.provider._find_claude",
        return_value="/usr/local/bin/claude",
    ):
        yield


def _cli_json(
    *,
    result: str = "hello",
    input_tokens: int = 100,
    output_tokens: int = 50,
    total_cost_usd: float = 0.01,
) -> str:
    return json.dumps(
        {
            "result": result,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
            "total_cost_usd": total_cost_usd,
        },
    )


def test_claude_cli_init_raises_when_claude_missing() -> None:
    """Raise when the claude binary is not on PATH."""
    with (
        patch("lintro.ai.providers.anthropic.provider._find_claude", return_value=None),
        pytest.raises(AINotAvailableError, match="claude"),
    ):
        AnthropicProvider(transport=AITransport.CLI)


def test_claude_cli_init_cli_transport_available(_mock_claude_on_path: None) -> None:
    """Report availability when the claude binary is discoverable."""
    provider = AnthropicProvider(transport=AITransport.CLI)
    assert_that(provider.is_available()).is_true()


async def test_claude_cli_complete_success(_mock_claude_on_path: None) -> None:
    """Parse JSON output from a successful claude --print invocation."""
    provider = AnthropicProvider(
        model="claude-sonnet-4-6",
        transport=AITransport.CLI,
        cli_bare=CliBareMode.ALWAYS,
    )
    stdout = _cli_json(result='{"summary": "ok"}')
    with patch_cli_exec(help_text=CLAUDE_HELP) as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        response = await provider.complete("Review this diff", system="Be concise")

    assert_that(response.content).contains("summary")
    assert_that(response.provider).is_equal_to(AIProvider.ANTHROPIC)
    cmd = mock_run.call_args.args[0]
    assert_that(cmd).contains("--bare", "--print", "--output-format", "json")
    assert_that(cmd).contains("--append-system-prompt", "Be concise")
    assert_that(cmd).contains("--model", "claude-sonnet-4-6")
    assert_that(cmd).does_not_contain("Review this diff")
    assert_that(mock_run.transport_calls[-1].input_text).is_equal_to(
        "Review this diff",
    )


async def test_claude_cli_complete_cli_schema_flag_when_requested(
    _mock_claude_on_path: None,
) -> None:
    """Pass --json-schema when cli_schema is provided."""
    from lintro.ai.json_response import CliSchemaRequest

    provider = AnthropicProvider(transport=AITransport.CLI)
    schema = CliSchemaRequest(
        schema={"type": "object"},
        schema_name="lintro_review",
    )
    stdout = _cli_json(result='{"summary": "ok"}')
    with patch_cli_exec(help_text=CLAUDE_HELP) as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        response = await provider.complete(
            "Review this diff",
            system="Be concise",
            cli_schema=schema,
        )

    assert_that(response.content).is_equal_to('{"summary": "ok"}')
    cmd = mock_run.transport_calls[-1].cmd
    assert_that(cmd).contains("--json-schema")
    schema_arg = cmd[cmd.index("--json-schema") + 1]
    assert_that(schema_arg).contains('"type"')


async def test_claude_cli_complete_json_schema_name_sent_when_cli_advertises_it(
    _mock_claude_on_path: None,
) -> None:
    """Send --json-schema-name only when the CLI --help advertises it."""
    from lintro.ai.json_response import CliSchemaRequest

    provider = AnthropicProvider(transport=AITransport.CLI)
    schema = CliSchemaRequest(
        schema={"type": "object"},
        schema_name="lintro_review",
    )
    completion = _cli_json(result='{"summary": "ok"}')

    def fake_run(
        cmd: list[str],
        *args: object,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        if "--help" in cmd:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout="  --json-schema-name <name>  Name the schema\n  --tools <list>\n",
                stderr="",
            )
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=completion,
            stderr="",
        )

    with patch_cli_exec(side_effect=fake_run) as mock_run:
        response = await provider.complete("Review this diff", cli_schema=schema)

    assert_that(response.content).is_equal_to('{"summary": "ok"}')
    completion_cmds = [
        call.cmd for call in mock_run.transport_calls if "--help" not in call.cmd
    ]
    assert_that(completion_cmds[-1]).contains("--json-schema-name", "lintro_review")


async def test_claude_cli_complete_json_schema_name_omitted_when_cli_lacks_it(
    _mock_claude_on_path: None,
) -> None:
    """Omit --json-schema-name (keep --json-schema) when the CLI dropped it.

    Regression for #1611: the current claude CLI errors with
    ``unknown option '--json-schema-name'``.
    """
    from lintro.ai.json_response import CliSchemaRequest

    provider = AnthropicProvider(transport=AITransport.CLI)
    schema = CliSchemaRequest(
        schema={"type": "object"},
        schema_name="lintro_review",
    )
    completion = _cli_json(result='{"summary": "ok"}')

    def fake_run(
        cmd: list[str],
        *args: object,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        if "--help" in cmd:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout="  --json-schema <schema>  Provide a JSON schema\n  --tools <list>\n",
                stderr="",
            )
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=completion,
            stderr="",
        )

    with patch_cli_exec(side_effect=fake_run) as mock_run:
        response = await provider.complete("Review this diff", cli_schema=schema)

    assert_that(response.content).is_equal_to('{"summary": "ok"}')
    completion_cmds = [
        call.cmd for call in mock_run.transport_calls if "--help" not in call.cmd
    ]
    assert_that(completion_cmds[-1]).does_not_contain("--json-schema-name")
    assert_that(completion_cmds[-1]).contains("--json-schema")


async def test_claude_cli_complete_auth_error(_mock_claude_on_path: None) -> None:
    """Surface authentication failures from claude stderr."""
    provider = AnthropicProvider(transport=AITransport.CLI)
    with (
        patch_cli_exec(help_text=CLAUDE_HELP) as mock_run,
        pytest.raises(AIAuthenticationError, match="login"),
    ):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="Authentication required. Please login.",
        )
        await provider.complete("hello")


def test_find_claude_found() -> None:
    """Return the claude path when shutil.which finds it."""
    with patch("shutil.which", return_value="/usr/local/bin/claude"):
        assert_that(_find_claude()).is_equal_to("/usr/local/bin/claude")


async def test_claude_cli_response_names_the_model_the_call_asked_for(
    _mock_claude_on_path: None,
) -> None:
    """A fallback call runs as ``--model <fallback>`` and its response says so (#2867)."""
    provider = AnthropicProvider(
        model="claude-sonnet-4-6",
        transport=AITransport.CLI,
        cli_bare=CliBareMode.ALWAYS,
    )
    stdout = _cli_json(result='{"summary": "ok"}')
    with patch_cli_exec(help_text=CLAUDE_HELP) as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        response = await provider.complete("Review this diff", model="claude-haiku-4-5")

    assert_that(response.model).is_equal_to("claude-haiku-4-5")
    assert_that(mock_run.transport_calls[-1].cmd).contains(
        "--model",
        "claude-haiku-4-5",
    )


# --- a renamed ai.api_key_env reaches the claude subprocess (#2549) ------------


@pytest.fixture()
def _no_ambient_claude_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip the literal key so only the renamed one can authenticate."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LINTRO_CLI_BARE", raising=False)


@pytest.mark.usefixtures("_no_ambient_claude_key")
async def test_a_renamed_api_key_env_reaches_the_subprocess(
    _mock_claude_on_path: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    """The child sees the renamed variable's value as ANTHROPIC_API_KEY (#2549).

    The parent process is untouched, and because the bare-mode decision reads
    the child environment, ``auto`` sends ``--bare`` for the re-exported key.
    """
    monkeypatch.setenv("MY_CLAUDE_KEY", "sk-ant-renamed")
    provider = AnthropicProvider(
        transport=AITransport.CLI,
        api_key_env="MY_CLAUDE_KEY",
        cli_bare=CliBareMode.AUTO,
    )
    with patch_cli_exec(help_text=CLAUDE_HELP) as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=_cli_json(),
            stderr="",
        )
        await provider.complete("Review this diff", repo_root=str(tmp_path))

    call = mock_run.transport_calls[-1]
    assert_that(call.extra_env).is_equal_to({"ANTHROPIC_API_KEY": "sk-ant-renamed"})
    child_env = mock_run.spawn_envs[-1]
    assert_that(child_env).is_not_none()
    assert_that(child_env["ANTHROPIC_API_KEY"]).is_equal_to("sk-ant-renamed")
    assert_that(call.cmd).contains("--bare")
    assert_that(os.environ).does_not_contain_key("ANTHROPIC_API_KEY")


@pytest.mark.usefixtures("_no_ambient_claude_key")
async def test_the_renamed_key_overrides_a_literal_one_for_the_child(
    _mock_claude_on_path: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    """With both set, the child runs on the configured variable's value."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-literal")
    monkeypatch.setenv("MY_CLAUDE_KEY", "sk-ant-renamed")
    provider = AnthropicProvider(
        transport=AITransport.CLI,
        api_key_env="MY_CLAUDE_KEY",
        cli_bare=CliBareMode.NEVER,
    )
    with patch_cli_exec(help_text=CLAUDE_HELP) as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=_cli_json(),
            stderr="",
        )
        await provider.complete("Review this diff", repo_root=str(tmp_path))

    child_env = mock_run.spawn_envs[-1]
    assert_that(child_env).is_not_none()
    assert_that(child_env["ANTHROPIC_API_KEY"]).is_equal_to("sk-ant-renamed")
    assert_that(os.environ["ANTHROPIC_API_KEY"]).is_equal_to("sk-ant-literal")


@pytest.mark.usefixtures("_no_ambient_claude_key")
async def test_the_default_api_key_env_adds_nothing_to_the_child(
    _mock_claude_on_path: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    """Without a rename the child inherits the environment unchanged."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-literal")
    provider = AnthropicProvider(transport=AITransport.CLI, cli_bare=CliBareMode.NEVER)
    with patch_cli_exec(help_text=CLAUDE_HELP) as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=_cli_json(),
            stderr="",
        )
        await provider.complete("Review this diff", repo_root=str(tmp_path))

    assert_that(mock_run.transport_calls[-1].extra_env).is_equal_to({})
    child_env = mock_run.spawn_envs[-1]
    assert_that(child_env).is_not_none()
    assert_that(child_env["ANTHROPIC_API_KEY"]).is_equal_to("sk-ant-literal")
