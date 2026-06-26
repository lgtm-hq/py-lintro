"""Tests for the Cursor AI provider (agent CLI wrapper)."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.ai.exceptions import (
    AIAuthenticationError,
    AINotAvailableError,
    AIProviderError,
)
from lintro.ai.providers.cursor import CURSOR_MIN_TIMEOUT, CursorProvider, _find_agent
from lintro.ai.registry import AIProvider


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
    """Create a CursorProvider with a mocked agent binary."""
    return CursorProvider()


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
        CursorProvider()


def test_cursor_provider_default_model(provider):
    """Use auto as the default model."""
    assert_that(provider.model_name).is_equal_to("auto")


@pytest.mark.usefixtures("_mock_agent_on_path")
def test_cursor_provider_custom_model():
    """Accept a custom model override."""
    p = CursorProvider(model="claude-opus-4-8-thinking-high")
    assert_that(p.model_name).is_equal_to("claude-opus-4-8-thinking-high")


def test_cursor_provider_is_available(provider):
    """Report available when agent binary is present."""
    assert_that(provider.is_available()).is_true()


# -- CursorProvider.complete() ---------------------------------------------


def test_complete_parses_successful_cli_json(provider):
    """Parse successful CLI JSON into AIResponse."""
    stdout = _cli_json(result="review output")
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        resp = provider.complete("Hello", repo_root="/tmp/repo")
    assert_that(resp.content).is_equal_to("review output")
    assert_that(resp.provider).is_equal_to(AIProvider.CURSOR)
    assert_that(resp.input_tokens).is_equal_to(100)
    assert_that(resp.output_tokens).is_equal_to(50)
    cmd = mock_run.call_args.args[0]
    assert_that(cmd).contains("--workspace", "/tmp/repo")


def test_complete_durable_session_uses_resume(provider):
    """Second call in a durable session resumes the CLI session id."""
    stdout = _cli_json(result="ok")
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        provider.begin_durable_session(repo_root="/tmp/repo")
        provider.complete("first", repo_root="/tmp/repo")
        provider.complete("second", repo_root="/tmp/repo")
        second_cmd = mock_run.call_args_list[1].args[0]
        assert_that(second_cmd).contains("--resume", "sess-123")


def test_complete_one_shot_skips_resume(provider):
    """One-shot calls do not resume an existing durable session."""
    stdout = _cli_json(result="ok")
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        provider.begin_durable_session(repo_root="/tmp/repo")
        provider.complete(
            "chunk",
            repo_root="/tmp/repo",
            use_one_shot=True,
        )
        cmd = mock_run.call_args.args[0]
        assert_that(cmd).does_not_contain("--resume")

    def test_timeout_floor_is_six_hundred_seconds(self, provider):
        """Enforce Cursor CLI minimum timeout of 600 seconds."""
        stdout = _cli_json(result="ok")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=stdout,
                stderr="",
            )
            provider.complete("Hello", timeout=120.0, repo_root="/tmp/repo")
        assert_that(mock_run.call_args.kwargs["timeout"]).is_equal_to(
            CURSOR_MIN_TIMEOUT,
        )


def test_complete_prepends_system_prompt_via_stdin(provider):
    """Prepend system prompt to user message via stdin."""
    stdout = _cli_json(result="ok")
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        provider.complete("user msg", system="sys prompt")
        call_kwargs = mock_run.call_args
        input_text = call_kwargs.kwargs.get("input", "")
        assert_that(input_text).contains("sys prompt")
        assert_that(input_text).contains("user msg")


def test_complete_raises_on_subprocess_timeout(provider):
    """Raise AIProviderError when CLI times out."""
    with (
        patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="agent", timeout=60),
        ),
        pytest.raises(AIProviderError, match="timed out"),
    ):
        provider.complete("Hello")


def test_complete_raises_auth_error(provider):
    """Raise AIAuthenticationError on auth failure stderr."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="Authentication required. Run 'agent login' first.",
        )
        with pytest.raises(AIAuthenticationError, match="login"):
            provider.complete("Hello")


def test_complete_raises_on_nonzero_exit(provider):
    """Raise AIProviderError on non-zero CLI exit code."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=2,
            stdout="",
            stderr="something broke",
        )
        with pytest.raises(AIProviderError, match="exited with code 2"):
            provider.complete("Hello")


def test_complete_raises_on_invalid_json_stdout(provider):
    """Raise AIProviderError when stdout is not valid JSON."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="not json at all",
            stderr="",
        )
        with pytest.raises(AIProviderError, match="invalid JSON"):
            provider.complete("Hello")


def test_complete_raises_on_cli_error_in_response(provider):
    """Raise AIProviderError when JSON reports is_error."""
    stdout = _cli_json(
        result="something failed",
        is_error=True,
        subtype="error",
    )
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        with pytest.raises(AIProviderError, match="reported error"):
            provider.complete("Hello")


def test_complete_appends_max_tokens_to_prompt(provider):
    """Append token budget constraint to the CLI stdin prompt."""
    stdout = _cli_json(result="ok")
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        provider.complete("Hello", max_tokens=512)
        input_text = mock_run.call_args.kwargs.get("input", "")
        assert_that(input_text).contains("Respond in at most 512 tokens")


def test_complete_uses_minimum_timeout_for_agent(provider):
    """Agent CLI calls enforce a minimum subprocess timeout."""
    stdout = _cli_json(result="ok")
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        provider.complete("Hello", timeout=45.0)
        assert_that(mock_run.call_args.kwargs.get("timeout")).is_equal_to(300.0)


def test_complete_cost_estimate_is_zero(provider):
    """Cursor subscription — per-token cost is always zero."""
    stdout = _cli_json(result="ok", input_tokens=5000, output_tokens=2000)
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        resp = provider.complete("Hello")
    assert_that(resp.cost_estimate).is_equal_to(0.0)


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


def test_extract_json_object_returns_empty_string_unchanged():
    """Return empty string unchanged."""
    assert_that(CursorProvider._extract_json_object("")).is_equal_to("")


def test_complete_preserves_plain_text_with_braces(provider):
    """Do not truncate plain-text answers that contain balanced braces."""
    result_text = "Use destructuring like { userId } in your handler."
    stdout = _cli_json(result=result_text)
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        resp = provider.complete("Hello")
    assert_that(resp.content).is_equal_to(result_text)
