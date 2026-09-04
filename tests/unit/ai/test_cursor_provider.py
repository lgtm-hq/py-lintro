"""Tests for the Cursor AI provider (agent CLI wrapper)."""

from __future__ import annotations

import json
import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from collections.abc import Callable
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.budget import CostBudget
from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import (
    AIAuthenticationError,
    AINotAvailableError,
    AIProviderError,
)
from lintro.ai.providers import get_provider
from lintro.ai.providers.cursor import CURSOR_MIN_TIMEOUT, CursorProvider, _find_agent
from lintro.ai.registry import AIProvider
from tests.unit.ai.conftest import HANG, patch_cli_exec


@pytest.fixture()
def _mock_agent_on_path():
    """Patch shutil.which to report ``agent`` as available."""
    with patch(
        "lintro.ai.providers.cursor._find_agent",
        return_value="/usr/local/bin/agent",
    ):
        yield


@pytest.fixture()
def provider(_mock_agent_on_path):
    """Create a CursorProvider with a mocked agent binary and trust opted out."""
    return CursorProvider(cursor_trust_workspace=False)


def _cli_json(
    result: str = "hello",
    input_tokens: int = 100,
    output_tokens: int = 50,
    is_error: bool = False,
    subtype: str = "success",
    session_id: str = "sess-123",
) -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": subtype,
            "is_error": is_error,
            "result": result,
            "session_id": session_id,
            "usage": {
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
            },
        },
    )


#: Trimmed ``agent --help`` output carrying the optional flags lintro gates on.
_AGENT_HELP = (
    "  -p, --print              Print responses to console\n"
    "  --output-format <fmt>    Output format\n"
    "  --resume [chatId]        Select a session to resume\n"
    "  --trust                  Trust the current workspace without prompting\n"
)


def _fake_run_with_probes(
    stdout: str,
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Build a ``subprocess.run`` stand-in that answers capability probes.

    The capability guard probes ``agent --version`` and ``agent --help`` before
    the real call; without realistic probe answers, gated optional flags such as
    ``--resume`` and ``--trust`` would be filtered out.

    Args:
        stdout: Stdout to return for the actual completion call.

    Returns:
        A callable suitable for ``patch_cli_exec(side_effect=...)``.
    """

    def _run(
        cmd: list[str],
        *args: object,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        if "--version" in cmd:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout="2026.07.09-a3815c0\n",
                stderr="",
            )
        if "--help" in cmd:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=_AGENT_HELP,
                stderr="",
            )
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=stdout,
            stderr="",
        )

    return _run


def _completion_calls(mock_run: MagicMock) -> list[list[str]]:
    """Return only the argv lists of real completion calls.

    Args:
        mock_run: The patched ``subprocess.run`` mock.

    Returns:
        Argv lists with ``--version`` / ``--help`` probes filtered out.
    """
    return [
        list(call.args[0])
        for call in mock_run.call_args_list
        if "--version" not in call.args[0] and "--help" not in call.args[0]
    ]


# -- _find_agent -----------------------------------------------------------


def test_find_agent_returns_path_when_on_path():
    """Return agent path when binary is on PATH."""
    with patch("shutil.which", return_value="/usr/local/bin/agent"):
        assert_that(_find_agent()).is_equal_to("/usr/local/bin/agent")


def test_find_agent_returns_none_when_missing():
    """Return None when agent binary is missing."""
    with patch("shutil.which", return_value=None):
        assert_that(_find_agent()).is_none()


# -- CursorProvider.__init__ -----------------------------------------------


def test_cursor_provider_raises_when_agent_missing():
    """Raise AINotAvailableError when agent CLI is missing."""
    with (
        patch(
            "lintro.ai.providers.cursor._find_agent",
            return_value=None,
        ),
        pytest.raises(AINotAvailableError, match="agent"),
    ):
        CursorProvider(cursor_trust_workspace=False)


def test_cursor_provider_requires_explicit_workspace_trust(
    _mock_agent_on_path: object,
) -> None:
    """Omitting ``cursor_trust_workspace`` is a TypeError, not a silent default.

    ``AIConfig.cursor_trust_workspace`` is the single default site (#2041), so
    the constructor deliberately carries no default of its own.
    """
    with pytest.raises(TypeError, match="cursor_trust_workspace"):
        CursorProvider()  # type: ignore[call-arg]


def test_cursor_provider_default_model(provider):
    """Use auto as the default model."""
    assert_that(provider.model_name).is_equal_to("auto")


@pytest.mark.usefixtures("_mock_agent_on_path")
def test_cursor_provider_custom_model():
    """Accept a custom model override."""
    p = CursorProvider(
        model="claude-opus-4-8-thinking-high",
        cursor_trust_workspace=False,
    )
    assert_that(p.model_name).is_equal_to("claude-opus-4-8-thinking-high")


async def test_cursor_provider_is_available(provider):
    """Report available when agent binary is present."""
    assert_that(provider.is_available()).is_true()


# -- CursorProvider.complete() ---------------------------------------------


async def test_complete_parses_successful_cli_json(provider):
    """Parse successful CLI JSON into AIResponse."""
    stdout = _cli_json(result="review output")
    with patch_cli_exec() as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        resp = await provider.complete("Hello", repo_root="/tmp/repo")
    assert_that(resp.content).is_equal_to("review output")
    assert_that(resp.provider).is_equal_to(AIProvider.CURSOR)
    assert_that(resp.input_tokens).is_equal_to(100)
    assert_that(resp.output_tokens).is_equal_to(50)
    cmd = mock_run.call_args.args[0]
    assert_that(cmd).contains("--workspace", "/tmp/repo")


async def test_complete_durable_session_uses_resume(provider):
    """Second call in a durable session resumes the CLI session id."""
    stdout = _cli_json(result="ok")
    with patch_cli_exec(side_effect=_fake_run_with_probes(stdout)) as mock_run:
        provider.begin_durable_session(repo_root="/tmp/repo")
        await provider.complete("first", repo_root="/tmp/repo")
        await provider.complete("second", repo_root="/tmp/repo")
        second_cmd = _completion_calls(mock_run)[1]
        assert_that(second_cmd).contains("--resume", "sess-123")


async def test_complete_one_shot_skips_resume(provider):
    """One-shot calls do not resume an existing durable session."""
    stdout = _cli_json(result="ok")
    with patch_cli_exec() as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        provider.begin_durable_session(repo_root="/tmp/repo")
        await provider.complete(
            "chunk",
            repo_root="/tmp/repo",
            use_one_shot=True,
        )
        cmd = mock_run.call_args.args[0]
        assert_that(cmd).does_not_contain("--resume")


async def test_timeout_floor_is_six_hundred_seconds(provider):
    """Enforce Cursor CLI minimum timeout of 600 seconds."""
    stdout = _cli_json(result="ok")
    with patch_cli_exec() as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        await provider.complete("Hello", timeout=120.0, repo_root="/tmp/repo")
    assert_that(mock_run.transport_calls[-1].timeout).is_equal_to(
        CURSOR_MIN_TIMEOUT,
    )


async def test_complete_prepends_system_prompt_via_stdin(provider):
    """Prepend system prompt to user message via stdin."""
    stdout = _cli_json(result="ok")
    with patch_cli_exec() as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        await provider.complete("user msg", system="sys prompt")
        input_text = mock_run.transport_calls[-1].input_text or ""
        assert_that(input_text).contains("sys prompt")
        assert_that(input_text).contains("user msg")


async def test_complete_raises_on_subprocess_timeout(provider):
    """Raise AIProviderError when CLI times out."""

    def _hang_completion(cmd: list[str]) -> object:
        """Answer the capability probes, then hang on the real call.

        Args:
            cmd: Argv the transport invoked.

        Returns:
            A probe result, or the HANG sentinel for the completion call.
        """
        if "--version" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "2026.07.09-a3815c0\n", "")
        if "--help" in cmd:
            return subprocess.CompletedProcess(cmd, 0, _AGENT_HELP, "")
        return HANG

    with (
        # The agent CLI enforces a 600s floor (covered separately), so the
        # floor is lowered here to keep the timeout path fast.
        patch("lintro.ai.providers.cursor.CURSOR_MIN_TIMEOUT", 0.01),
        patch_cli_exec(side_effect=_hang_completion),
        pytest.raises(AIProviderError, match="timed out"),
    ):
        await provider.complete("Hello", timeout=0.01)


async def test_complete_raises_auth_error(provider):
    """Raise AIAuthenticationError on auth failure stderr."""
    with patch_cli_exec() as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="Authentication required. Run 'agent login' first.",
        )
        with pytest.raises(AIAuthenticationError, match="login"):
            await provider.complete("Hello")


async def test_complete_raises_on_nonzero_exit(provider):
    """Raise AIProviderError on non-zero CLI exit code."""
    with patch_cli_exec() as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=2,
            stdout="",
            stderr="something broke",
        )
        with pytest.raises(AIProviderError, match="exited with code 2"):
            await provider.complete("Hello")


async def test_complete_recovers_prose_stdout(provider):
    """Recover a non-JSON envelope as unstructured prose instead of failing."""
    with patch_cli_exec() as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="not json at all",
            stderr="",
        )
        response = await provider.complete("Hello")
    assert_that(response.content).is_equal_to("not json at all")


async def test_complete_raises_on_blank_stdout(provider):
    """Raise AIProviderError when stdout carries no recoverable text."""
    with patch_cli_exec() as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="   \n  ",
            stderr="",
        )
        with pytest.raises(AIProviderError, match="invalid JSON"):
            await provider.complete("Hello")


async def test_complete_raises_on_cli_error_in_response(provider):
    """Raise AIProviderError when JSON reports is_error."""
    stdout = _cli_json(
        result="something failed",
        is_error=True,
        subtype="error",
    )
    with patch_cli_exec() as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        with pytest.raises(AIProviderError, match="reported error"):
            await provider.complete("Hello")


async def test_complete_appends_max_tokens_to_prompt(provider):
    """Append token budget constraint to the CLI stdin prompt."""
    stdout = _cli_json(result="ok")
    with patch_cli_exec() as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        await provider.complete("Hello", max_tokens=512)
        input_text = mock_run.transport_calls[-1].input_text or ""
        assert_that(input_text).contains("Respond in at most 512 tokens")


async def test_complete_uses_minimum_timeout_for_agent(provider):
    """Agent CLI calls enforce a minimum subprocess timeout."""
    stdout = _cli_json(result="ok")
    with patch_cli_exec() as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        await provider.complete("Hello", timeout=45.0)
        assert_that(mock_run.transport_calls[-1].timeout).is_equal_to(
            CURSOR_MIN_TIMEOUT,
        )


async def test_complete_estimates_nonzero_cost_from_cli_usage(provider):
    """Cursor prices reported usage with a non-zero floor for the budget."""
    stdout = _cli_json(result="ok", input_tokens=5000, output_tokens=2000)
    with patch_cli_exec() as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        resp = await provider.complete("Hello")
    assert_that(resp.input_tokens).is_equal_to(5000)
    assert_that(resp.output_tokens).is_equal_to(2000)
    assert_that(resp.cost_estimate).is_greater_than(0.0)


async def test_complete_estimates_tokens_when_cli_omits_usage(provider):
    """When the CLI omits usage, tokens are estimated locally from text."""
    stdout = _cli_json(
        result="a fairly long review answer",
        input_tokens=0,
        output_tokens=0,
    )
    with patch_cli_exec() as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        resp = await provider.complete("Hello there, please review this code carefully")
    assert_that(resp.input_tokens).is_greater_than(0)
    assert_that(resp.output_tokens).is_greater_than(0)
    assert_that(resp.cost_estimate).is_greater_than(0.0)


async def test_cursor_cost_accrues_into_budget(provider):
    """A Cursor response's estimated cost is recorded by CostBudget."""
    stdout = _cli_json(result="ok", input_tokens=5000, output_tokens=2000)
    budget = CostBudget(max_cost_usd=1.0)
    with patch_cli_exec() as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        resp = await provider.complete("Hello")
    budget.record(resp.cost_estimate)
    assert_that(budget.spent).is_greater_than(0.0)


async def test_complete_omits_trust_flag_when_trust_opted_out(
    provider: CursorProvider,
) -> None:
    """CursorProvider built with cursor_trust_workspace=False omits '--trust'."""
    stdout = _cli_json(result="ok")
    with patch_cli_exec() as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        await provider.complete("Hello", repo_root="/tmp/repo")
    cmd = mock_run.call_args.args[0]
    assert_that(cmd).does_not_contain("--trust")


async def test_complete_includes_trust_flag_when_constructed_with_trust(
    _mock_agent_on_path: object,
) -> None:
    """CursorProvider constructed with trust enabled appends '--trust'."""
    trusting = CursorProvider(cursor_trust_workspace=True)
    stdout = _cli_json(result="ok")
    with patch_cli_exec(side_effect=_fake_run_with_probes(stdout)) as mock_run:
        await trusting.complete("Hello", repo_root="/tmp/repo")
    cmd = _completion_calls(mock_run)[-1]
    assert_that(cmd).contains("--trust")


async def test_complete_includes_trust_flag_by_default(
    _mock_agent_on_path: object,
) -> None:
    """The '--trust' flag is appended when AIConfig uses the default."""
    config = AIConfig(
        provider=AIProvider.CURSOR,
        transport=AITransport.CLI,
    )
    cursor = get_provider(config)
    stdout = _cli_json(result="ok")
    with patch_cli_exec(side_effect=_fake_run_with_probes(stdout)) as mock_run:
        await cursor.complete("Hello", repo_root="/tmp/repo")
    cmd = _completion_calls(mock_run)[-1]
    assert_that(cmd).contains("--trust")


async def test_complete_omits_trust_flag_when_opted_out(
    _mock_agent_on_path: object,
) -> None:
    """The '--trust' flag is absent when workspace trust is explicitly false."""
    config = AIConfig(
        provider=AIProvider.CURSOR,
        transport=AITransport.CLI,
        cursor_trust_workspace=False,
    )
    cursor = get_provider(config)
    stdout = _cli_json(result="ok")
    with patch_cli_exec(side_effect=_fake_run_with_probes(stdout)) as mock_run:
        await cursor.complete("Hello", repo_root="/tmp/repo")
    cmd = _completion_calls(mock_run)[-1]
    assert_that(cmd).does_not_contain("--trust")


# -- CursorProvider._extract_json_object() ---------------------------------


def test_extract_json_object_returns_clean_json_unchanged():
    """Return clean JSON unchanged."""
    obj = '{"a": 1}'
    assert_that(CursorProvider._extract_json_object(obj)).is_equal_to(obj)


def test_extract_json_object_strips_leading_prose():
    """Extract JSON object after leading prose."""
    text = 'Some preamble text.\n{"summary": "ok", "findings": []}'
    assert_that(
        CursorProvider._extract_json_object(text),
    ).is_equal_to('{"summary": "ok", "findings": []}')


def test_extract_json_object_handles_nested_braces():
    """Handle nested JSON objects correctly."""
    text = 'Preamble\n{"a": {"b": 1}, "c": 2}'
    assert_that(
        CursorProvider._extract_json_object(text),
    ).is_equal_to('{"a": {"b": 1}, "c": 2}')


def test_extract_json_object_ignores_braces_in_strings():
    """Ignore braces inside JSON string values."""
    text = '{"key": "value with { brace }"}'
    assert_that(
        CursorProvider._extract_json_object(text),
    ).is_equal_to(text)


def test_extract_json_object_returns_original_when_no_json():
    """Return original text when no JSON is present."""
    text = "no json here"
    assert_that(
        CursorProvider._extract_json_object(text),
    ).is_equal_to(text)


async def test_extract_json_object_returns_empty_string_unchanged():
    """Return empty string unchanged."""
    assert_that(CursorProvider._extract_json_object("")).is_equal_to("")


async def test_complete_preserves_plain_text_with_braces(provider):
    """Do not truncate plain-text answers that contain balanced braces."""
    result_text = "Use destructuring like { userId } in your handler."
    stdout = _cli_json(result=result_text)
    with patch_cli_exec() as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        resp = await provider.complete("Hello")
    assert_that(resp.content).is_equal_to(result_text)
