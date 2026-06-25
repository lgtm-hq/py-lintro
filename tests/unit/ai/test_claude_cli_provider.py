"""Tests for Anthropic Claude CLI provider backend."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.ai.enums import AITransport
from lintro.ai.exceptions import AIAuthenticationError, AINotAvailableError
from lintro.ai.providers.anthropic import AnthropicProvider, _find_claude
from lintro.ai.registry import AIProvider


@pytest.fixture()
def _mock_claude_on_path() -> Iterator[None]:
    """Patch claude binary discovery for CLI transport tests."""
    with patch(
        "lintro.ai.providers.anthropic._find_claude",
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


class TestClaudeCliInit:
    """Tests for CLI transport initialization."""

    def test_raises_when_claude_missing(self) -> None:
        """Raise when the claude binary is not on PATH."""
        with (
            patch("lintro.ai.providers.anthropic._find_claude", return_value=None),
            pytest.raises(AINotAvailableError, match="claude"),
        ):
            AnthropicProvider(transport=AITransport.CLI)

    def test_cli_transport_available(self, _mock_claude_on_path: None) -> None:
        """Report availability when the claude binary is discoverable."""
        provider = AnthropicProvider(transport=AITransport.CLI)
        assert_that(provider.is_available()).is_true()


class TestClaudeCliComplete:
    """Tests for claude -p completions."""

    def test_success(self, _mock_claude_on_path: None) -> None:
        """Parse JSON output from a successful claude -p invocation."""
        provider = AnthropicProvider(
            model="claude-sonnet-4-6",
            transport=AITransport.CLI,
        )
        stdout = _cli_json(result='{"summary": "ok"}')
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=stdout,
                stderr="",
            )
            response = provider.complete("Review this diff", system="Be concise")

        assert_that(response.content).contains("summary")
        assert_that(response.provider).is_equal_to(AIProvider.ANTHROPIC)
        cmd = mock_run.call_args.args[0]
        assert_that(cmd).contains("--bare", "-p", "--output-format", "json")
        assert_that(cmd).contains("--append-system-prompt", "Be concise")
        assert_that(cmd).contains("--model", "claude-sonnet-4-6")

    def test_cli_schema_flag_when_requested(self, _mock_claude_on_path: None) -> None:
        """Pass --json-schema when cli_schema is provided."""
        from lintro.ai.json_response import CliSchemaRequest

        provider = AnthropicProvider(transport=AITransport.CLI)
        schema = CliSchemaRequest(
            schema={"type": "object"},
            schema_name="lintro_review",
        )
        stdout = _cli_json(result='{"summary": "ok"}')
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=stdout,
                stderr="",
            )
            provider.complete(
                "Review this diff",
                system="Be concise",
                cli_schema=schema,
            )

        cmd = mock_run.call_args.args[0]
        assert_that(cmd).contains("--json-schema")
        schema_arg = cmd[cmd.index("--json-schema") + 1]
        assert_that(schema_arg).contains('"type"')

    def test_auth_error(self, _mock_claude_on_path: None) -> None:
        """Surface authentication failures from claude stderr."""
        provider = AnthropicProvider(transport=AITransport.CLI)
        with (
            patch("subprocess.run") as mock_run,
            pytest.raises(AIAuthenticationError, match="login"),
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout="",
                stderr="Authentication required. Please login.",
            )
            provider.complete("hello")


class TestFindClaude:
    """Tests for claude binary discovery."""

    def test_found(self) -> None:
        """Return the claude path when shutil.which finds it."""
        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            assert_that(_find_claude()).is_equal_to("/usr/local/bin/claude")
