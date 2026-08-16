"""CLI providers deliver the prompt via stdin, not argv (#1967)."""

from __future__ import annotations

import subprocess  # nosec B404 - CompletedProcess fixtures only; no process spawn
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.ai.enums import AITransport, CliBareMode
from lintro.ai.providers.anthropic import AnthropicProvider
from lintro.ai.providers.openai import OpenAIProvider
from tests.unit.ai.conftest import patch_cli_exec


@pytest.fixture()
def _mock_claude_on_path() -> Iterator[None]:
    """Patch claude binary discovery for CLI transport tests."""
    with patch(
        "lintro.ai.providers.anthropic._find_claude",
        return_value="/usr/local/bin/claude",
    ):
        yield


@pytest.fixture()
def _mock_codex_on_path() -> Iterator[None]:
    """Patch codex binary discovery for CLI transport tests."""
    with patch(
        "lintro.ai.providers.openai._find_codex",
        return_value="/usr/local/bin/codex",
    ):
        yield


def _large_prompt(*, chars: int = 200_000) -> str:
    """Build a prompt larger than Linux MAX_ARG_STRLEN (128 KiB)."""
    return "DIFF\n" + ("x" * chars)


async def test_claude_cli_prompt_uses_stdin_not_argv(
    _mock_claude_on_path: None,
) -> None:
    """Claude argv stays O(1) while a huge prompt rides on stdin."""
    provider = AnthropicProvider(
        transport=AITransport.CLI,
        cli_bare=CliBareMode.ALWAYS,
    )
    prompt = _large_prompt()
    with patch_cli_exec() as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"result":"ok","usage":{"input_tokens":1,"output_tokens":1}}',
            stderr="",
        )
        await provider.complete(prompt)

    cmd = mock_run.transport_calls[-1].cmd
    argv_bytes = sum(len(part.encode()) for part in cmd)
    assert_that(argv_bytes).is_less_than(8_000)
    assert_that(cmd).does_not_contain(prompt)
    assert_that(mock_run.transport_calls[-1].input_text).is_equal_to(prompt)
    assert_that(cmd).contains("--print")


async def test_codex_cli_prompt_uses_stdin_not_argv(
    _mock_codex_on_path: None,
) -> None:
    """Codex argv stays O(1) while a huge prompt rides on stdin."""
    provider = OpenAIProvider(transport=AITransport.CLI)
    prompt = _large_prompt()
    with patch_cli_exec() as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                '{"type":"item.completed","item":{"type":"agent_message",'
                '"text":"ok"}}\n'
                '{"type":"turn.completed","usage":{"input_tokens":1,'
                '"output_tokens":1}}\n'
            ),
            stderr="",
        )
        await provider.complete(prompt, repo_root="/tmp/repo")

    cmd = mock_run.transport_calls[-1].cmd
    argv_bytes = sum(len(part.encode()) for part in cmd)
    assert_that(argv_bytes).is_less_than(8_000)
    assert_that(cmd).does_not_contain(prompt)
    assert_that(cmd[-1]).is_equal_to("-")
    assert_that(mock_run.transport_calls[-1].input_text).is_equal_to(prompt)


async def test_claude_cli_argv_length_independent_of_diff_size(
    _mock_claude_on_path: None,
) -> None:
    """Doubling the prompt must not grow argv (#1967)."""
    provider = AnthropicProvider(
        transport=AITransport.CLI,
        cli_bare=CliBareMode.NEVER,
    )
    small = "small"
    large = _large_prompt(chars=300_000)
    argv_lengths: list[int] = []
    for prompt in (small, large):
        with patch_cli_exec() as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout='{"result":"ok","usage":{"input_tokens":1,"output_tokens":1}}',
                stderr="",
            )
            await provider.complete(prompt)
        cmd = mock_run.transport_calls[-1].cmd
        argv_lengths.append(sum(len(part.encode()) for part in cmd))

    assert_that(argv_lengths[0]).is_equal_to(argv_lengths[1])
