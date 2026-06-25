"""Tests for OpenAI Codex CLI provider backend."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.ai.enums import AITransport
from lintro.ai.exceptions import AIAuthenticationError, AINotAvailableError
from lintro.ai.providers.openai import OpenAIProvider, _find_codex
from lintro.ai.registry import AIProvider


@pytest.fixture()
def _mock_codex_on_path():
    with patch(
        "lintro.ai.providers.openai._find_codex",
        return_value="/usr/local/bin/codex",
    ):
        yield


def _jsonl_response(*, text: str = '{"summary": "ok"}') -> str:
    lines = [
        json.dumps({
            "type": "item.completed",
            "item": {"type": "agent_message", "text": text},
        }),
        json.dumps({
            "type": "turn.completed",
            "usage": {"input_tokens": 120, "output_tokens": 40},
        }),
    ]
    return "\n".join(lines)


class TestCodexCliInit:
    """Tests for Codex CLI transport initialization."""

    def test_raises_when_codex_missing(self) -> None:
        with (
            patch("lintro.ai.providers.openai._find_codex", return_value=None),
            pytest.raises(AINotAvailableError, match="codex"),
        ):
            OpenAIProvider(transport=AITransport.CLI)

    def test_cli_transport_available(self, _mock_codex_on_path) -> None:
        provider = OpenAIProvider(transport=AITransport.CLI)
        assert_that(provider.is_available()).is_true()


class TestCodexCliComplete:
    """Tests for codex exec completions."""

    def test_success(self, _mock_codex_on_path) -> None:
        provider = OpenAIProvider(transport=AITransport.CLI)
        stdout = _jsonl_response()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=stdout,
                stderr="",
            )
            response = provider.complete("Review this diff", repo_root="/tmp/repo")

        assert_that(response.content).contains("summary")
        assert_that(response.provider).is_equal_to(AIProvider.OPENAI)
        cmd = mock_run.call_args.args[0]
        assert_that(cmd).contains("exec", "--json", "--sandbox", "read-only")

    def test_auth_error(self, _mock_codex_on_path) -> None:
        provider = OpenAIProvider(transport=AITransport.CLI)
        with (
            patch("subprocess.run") as mock_run,
            pytest.raises(AIAuthenticationError, match="login"),
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout="",
                stderr="Not authenticated. Run codex login.",
            )
            provider.complete("hello", repo_root="/tmp/repo")


class TestFindCodex:
    """Tests for codex binary discovery."""

    def test_found(self) -> None:
        with patch("shutil.which", return_value="/usr/local/bin/codex"):
            assert_that(_find_codex()).is_equal_to("/usr/local/bin/codex")
