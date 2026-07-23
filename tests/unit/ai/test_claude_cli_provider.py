"""Tests for Anthropic Claude CLI provider backend."""

from __future__ import annotations

import json
import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
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

    def test_json_schema_name_sent_when_cli_advertises_it(
        self,
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
                    stdout="  --json-schema-name <name>  Name the schema\n",
                    stderr="",
                )
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=completion,
                stderr="",
            )

        with patch("subprocess.run", side_effect=fake_run) as mock_run:
            provider.complete("Review this diff", cli_schema=schema)

        completion_calls = [
            call for call in mock_run.call_args_list if "--help" not in call.args[0]
        ]
        cmd = completion_calls[-1].args[0]
        assert_that(cmd).contains("--json-schema-name", "lintro_review")

    def test_json_schema_name_omitted_when_cli_lacks_it(
        self,
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
                    stdout="  --json-schema <schema>  Provide a JSON schema\n",
                    stderr="",
                )
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=completion,
                stderr="",
            )

        with patch("subprocess.run", side_effect=fake_run) as mock_run:
            provider.complete("Review this diff", cli_schema=schema)

        completion_calls = [
            call for call in mock_run.call_args_list if "--help" not in call.args[0]
        ]
        cmd = completion_calls[-1].args[0]
        assert_that(cmd).does_not_contain("--json-schema-name")
        assert_that(cmd).contains("--json-schema")

    def test_supports_json_schema_name_probe_is_cached(
        self,
        _mock_claude_on_path: None,
    ) -> None:
        """The --help capability probe runs once and caches its result."""
        from lintro.ai.providers.anthropic import _AnthropicCliTransport

        transport = _AnthropicCliTransport(
            binary_path="/usr/local/bin/claude",
            model="claude-sonnet-4-6",
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="  --json-schema-name <name>\n",
                stderr="",
            )
            assert_that(transport.supports_json_schema_name()).is_true()
            assert_that(transport.supports_json_schema_name()).is_true()

        assert_that(mock_run.call_count).is_equal_to(1)

    def test_supports_json_schema_name_false_on_probe_error(
        self,
        _mock_claude_on_path: None,
    ) -> None:
        """A failed --help probe reports the flag unsupported (send neither)."""
        from lintro.ai.providers.anthropic import _AnthropicCliTransport

        transport = _AnthropicCliTransport(
            binary_path="/usr/local/bin/claude",
            model="claude-sonnet-4-6",
        )
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            assert_that(transport.supports_json_schema_name()).is_false()

    def test_supports_json_schema_name_false_on_nonzero_help(
        self,
        _mock_claude_on_path: None,
    ) -> None:
        """A non-zero --help exit is unsupported even if it echoes the flag."""
        from lintro.ai.providers.anthropic import _AnthropicCliTransport

        transport = _AnthropicCliTransport(
            binary_path="/usr/local/bin/claude",
            model="claude-sonnet-4-6",
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout="",
                stderr="error near --json-schema-name\n",
            )
            assert_that(transport.supports_json_schema_name()).is_false()

    def test_supports_json_schema_name_false_on_permission_error(
        self,
        _mock_claude_on_path: None,
    ) -> None:
        """A PermissionError spawning the probe reports the flag unsupported."""
        from lintro.ai.providers.anthropic import _AnthropicCliTransport

        transport = _AnthropicCliTransport(
            binary_path="/usr/local/bin/claude",
            model="claude-sonnet-4-6",
        )
        with patch("subprocess.run", side_effect=PermissionError()):
            assert_that(transport.supports_json_schema_name()).is_false()

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
