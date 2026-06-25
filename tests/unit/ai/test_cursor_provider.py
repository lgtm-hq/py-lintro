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
    session_id: str = "sess-123",
) -> str:
    return json.dumps({
        "type": "result",
        "subtype": subtype,
        "is_error": is_error,
        "result": result,
        "session_id": session_id,
        "usage": {
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
        },
    })


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
        provider = CursorProvider(model="gpt-5.3-codex-fast")
        assert_that(provider.model_name).is_equal_to("gpt-5.3-codex-fast")

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
            resp = provider.complete("Hello", repo_root="/tmp/repo")
        assert_that(resp.content).is_equal_to("review output")
        assert_that(resp.provider).is_equal_to(AIProvider.CURSOR)
        cmd = mock_run.call_args.args[0]
        assert_that(cmd).contains("--workspace", "/tmp/repo")

    def test_durable_session_uses_resume(self, provider):
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

    def test_one_shot_skips_resume(self, provider):
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
            provider.complete("Hello", repo_root="/tmp/repo")

    def test_cost_estimate_zero(self, provider):
        stdout = _cli_json(result="ok", input_tokens=5000, output_tokens=2000)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=stdout,
                stderr="",
            )
            resp = provider.complete("Hello", repo_root="/tmp/repo")
        assert_that(resp.cost_estimate).is_equal_to(0.0)


class TestExtractJsonObject:
    """Tests for the JSON extraction helper."""

    def test_prose_before_json(self):
        text = 'Some preamble text.\n{"summary": "ok", "findings": []}'
        assert_that(
            CursorProvider._extract_json_object(text),
        ).is_equal_to('{"summary": "ok", "findings": []}')
