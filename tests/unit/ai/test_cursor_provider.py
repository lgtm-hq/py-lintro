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
from lintro.ai.providers.cursor import CursorProvider, _find_agent
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
) -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": subtype,
            "is_error": is_error,
            "result": result,
            "usage": {
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
            },
        }
    )


class TestFindAgent:
    """Tests for agent binary discovery."""

    def test_found(self):
        with patch("shutil.which", return_value="/usr/local/bin/agent"):
            assert_that(_find_agent()).is_equal_to("/usr/local/bin/agent")

    def test_not_found(self):
        with patch("shutil.which", return_value=None):
            assert_that(_find_agent()).is_none()


class TestCursorProviderInit:
    """Tests for CursorProvider initialization."""

    def test_raises_when_agent_missing(self):
        with (
            patch(
                "lintro.ai.providers.cursor._find_agent",
                return_value=None,
            ),
            pytest.raises(AINotAvailableError, match="agent"),
        ):
            CursorProvider()

    def test_default_model(self, provider):
        assert_that(provider.model_name).is_equal_to("auto")

    def test_custom_model(self, _mock_agent_on_path):
        p = CursorProvider(model="claude-opus-4-8-thinking-high")
        assert_that(p.model_name).is_equal_to("claude-opus-4-8-thinking-high")

    def test_is_available(self, provider):
        assert_that(provider.is_available()).is_true()


class TestComplete:
    """Tests for CursorProvider.complete()."""

    def test_success(self, provider):
        stdout = _cli_json(result="review output")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=stdout,
                stderr="",
            )
            resp = provider.complete("Hello")
        assert_that(resp.content).is_equal_to("review output")
        assert_that(resp.provider).is_equal_to(AIProvider.CURSOR)
        assert_that(resp.input_tokens).is_equal_to(100)
        assert_that(resp.output_tokens).is_equal_to(50)

    def test_system_prompt_prepended(self, provider):
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

    def test_timeout_raises(self, provider):
        with (
            patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="agent", timeout=60),
            ),
            pytest.raises(AIProviderError, match="timed out"),
        ):
            provider.complete("Hello")

    def test_auth_error(self, provider):
        with (
            patch("subprocess.run") as mock_run,
            pytest.raises(AIAuthenticationError, match="login"),
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout="",
                stderr="Authentication required. Run 'agent login' first.",
            )
            provider.complete("Hello")

    def test_nonzero_exit(self, provider):
        with (
            patch("subprocess.run") as mock_run,
            pytest.raises(AIProviderError, match="exited with code 2"),
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=2,
                stdout="",
                stderr="something broke",
            )
            provider.complete("Hello")

    def test_invalid_json_stdout(self, provider):
        with (
            patch("subprocess.run") as mock_run,
            pytest.raises(AIProviderError, match="invalid JSON"),
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="not json at all",
                stderr="",
            )
            provider.complete("Hello")

    def test_cli_error_in_response(self, provider):
        stdout = _cli_json(
            result="something failed",
            is_error=True,
            subtype="error",
        )
        with (
            patch("subprocess.run") as mock_run,
            pytest.raises(AIProviderError, match="reported error"),
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=stdout,
                stderr="",
            )
            provider.complete("Hello")

    def test_cost_estimate_zero(self, provider):
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


class TestExtractJsonObject:
    """Tests for the JSON extraction helper."""

    def test_clean_json(self):
        obj = '{"a": 1}'
        assert_that(CursorProvider._extract_json_object(obj)).is_equal_to(obj)

    def test_prose_before_json(self):
        text = 'Some preamble text.\n{"summary": "ok", "findings": []}'
        assert_that(
            CursorProvider._extract_json_object(text),
        ).is_equal_to('{"summary": "ok", "findings": []}')

    def test_nested_braces(self):
        text = 'Preamble\n{"a": {"b": 1}, "c": 2}'
        assert_that(
            CursorProvider._extract_json_object(text),
        ).is_equal_to('{"a": {"b": 1}, "c": 2}')

    def test_braces_in_strings(self):
        text = '{"key": "value with { brace }"}'
        assert_that(
            CursorProvider._extract_json_object(text),
        ).is_equal_to(text)

    def test_no_json(self):
        text = "no json here"
        assert_that(
            CursorProvider._extract_json_object(text),
        ).is_equal_to(text)

    def test_empty_string(self):
        assert_that(CursorProvider._extract_json_object("")).is_equal_to("")
