"""CLI transports recover prose envelopes and never truncate evidence (#1853).

A ``claude``/``agent``/``codex`` invocation that exits zero but answers in prose
used to be rejected wholesale, with the evidence cut to ``stdout[:500]``. These
tests pin the replacement: the prose comes back as unstructured content, and any
genuinely unrecoverable output is reported in full and written to disk.
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 - subprocess types drive the CLI doubles under test
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.ai.enums import AITransport, CliBareMode
from lintro.ai.exceptions import AIProviderError
from lintro.ai.providers.anthropic.provider import AnthropicProvider
from lintro.ai.providers.base import BaseAIProvider
from lintro.ai.providers.cursor.provider import CursorProvider
from lintro.ai.providers.openai.provider import OpenAIProvider
from lintro.ai.raw_response import RAW_RESPONSE_DIR
from tests.unit.ai.conftest import CLAUDE_HELP, patch_cli_exec

_PROSE = (
    "Reviewed the four commits. Two actionable findings:\n\n"
    "1. The observer re-arms after close (SearchDropdown.astro:258)\n"
) + ("detail " * 400)


@pytest.fixture(autouse=True)
def _isolate_captures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Write raw-response captures into a temporary workspace.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.chdir(tmp_path)


@pytest.fixture()
def _binaries_on_path() -> Iterator[None]:
    """Patch every CLI binary lookup used by these tests.

    Yields:
        None: all three provider binary probes are patched for the test body.
    """
    with (
        patch(
            "lintro.ai.providers.anthropic.provider._find_claude",
            return_value="/usr/local/bin/claude",
        ),
        patch(
            "lintro.ai.providers.cursor.provider._find_agent",
            return_value="/usr/local/bin/agent",
        ),
        patch(
            "lintro.ai.providers.openai.provider._find_codex",
            return_value="/usr/local/bin/codex",
        ),
    ):
        yield


def _completed(stdout: str) -> subprocess.CompletedProcess[str]:
    """Return a clean-exit completed process carrying *stdout*.

    Args:
        stdout: Standard output text.

    Returns:
        A zero-exit completed process.
    """
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def _providers() -> dict[str, BaseAIProvider]:
    """Return one CLI-transport provider per implementation.

    Returns:
        Mapping of provider label to provider instance.
    """
    return {
        "anthropic": AnthropicProvider(
            transport=AITransport.CLI,
            cli_bare=CliBareMode.NEVER,
        ),
        "cursor": CursorProvider(
            transport=AITransport.CLI,
            cursor_trust_workspace=True,
        ),
        "openai": OpenAIProvider(transport=AITransport.CLI),
    }


@pytest.mark.parametrize("name", ["anthropic", "cursor", "openai"])
async def test_prose_envelope_is_recovered_in_full(
    name: str,
    _binaries_on_path: None,
) -> None:
    """Prose stdout becomes unstructured content instead of an error."""
    provider = _providers()[name]

    with patch_cli_exec(help_text=CLAUDE_HELP) as mock_run:
        mock_run.return_value = _completed(_PROSE)
        response = await provider.complete("Review this")

    assert_that(response.content).is_equal_to(_PROSE.strip())


@pytest.mark.parametrize("name", ["anthropic", "cursor", "openai"])
async def test_prose_envelope_is_persisted(
    name: str,
    tmp_path: Path,
    _binaries_on_path: None,
) -> None:
    """The complete prose response is written to the capture directory."""
    provider = _providers()[name]

    with patch_cli_exec(help_text=CLAUDE_HELP) as mock_run:
        mock_run.return_value = _completed(_PROSE)
        await provider.complete("Review this")

    captures = list((tmp_path / RAW_RESPONSE_DIR).glob("*.txt"))
    assert_that(captures).is_not_empty()
    assert_that(captures[0].read_text(encoding="utf-8")).is_equal_to(_PROSE)


@pytest.mark.parametrize("name", ["anthropic", "cursor"])
async def test_blank_envelope_reports_untruncated_evidence(
    name: str,
    _binaries_on_path: None,
) -> None:
    """An unrecoverable envelope still carries every character it produced."""
    provider = _providers()[name]
    # Whitespace-only stdout carries no answer to recover, so it stays an error;
    # the evidence block must still name the full-output capture.
    with patch_cli_exec(help_text=CLAUDE_HELP) as mock_run:
        mock_run.return_value = _completed("   \n\t ")
        with pytest.raises(AIProviderError) as excinfo:
            await provider.complete("Review this")

    assert_that(str(excinfo.value)).contains("Full raw output")
    assert_that(str(excinfo.value)).does_not_contain("Raw output:")


@pytest.mark.parametrize("name", ["anthropic", "cursor", "openai"])
async def test_prose_with_an_inline_json_span_is_not_reduced_to_it(
    name: str,
    _binaries_on_path: None,
) -> None:
    """An incidental ``{...}`` inside prose must not replace the whole answer."""
    provider = _providers()[name]
    prose = f'Finding 1: the config `{{"retries": 3}}` is wrong.\n{_PROSE}'

    with patch_cli_exec(help_text=CLAUDE_HELP) as mock_run:
        mock_run.return_value = _completed(prose)
        response = await provider.complete("Review this")

    assert_that(response.content).is_equal_to(prose.strip())


@pytest.mark.parametrize("name", ["anthropic", "cursor"])
async def test_error_envelope_evidence_is_never_truncated_to_500_chars(
    name: str,
    _binaries_on_path: None,
) -> None:
    """The historical ``stdout[:500]`` cap is gone from the error path too."""
    provider = _providers()[name]
    # An ``is_error`` envelope with no ``result`` used to fall back to
    # ``stdout[:500]``; the whole envelope must now reach the user.
    envelope = json.dumps({"is_error": True, "result": "", "note": _PROSE})
    tail = _PROSE[-40:]

    with patch_cli_exec(help_text=CLAUDE_HELP) as mock_run:
        mock_run.return_value = _completed(envelope)
        with pytest.raises(AIProviderError) as excinfo:
            await provider.complete("Review this")

    assert_that(len(envelope)).is_greater_than(500)
    assert_that(str(excinfo.value)).contains(tail)


async def test_claude_error_envelope_keeps_its_api_error_markers(
    _binaries_on_path: None,
) -> None:
    """``terminal_reason`` and ``api_error_status`` ride along with the prose.

    The output-exhaustion classifier tells a rejected request apart from an
    overrun by these fields, so dropping them would let a 429 whose prose
    mentions output tokens trigger a chunk split (#2695).
    """
    provider = _providers()["anthropic"]
    envelope = json.dumps(
        {
            "is_error": True,
            "terminal_reason": "api_error",
            "api_error_status": 429,
            "result": "maximum output tokens for your plan this hour",
        },
    )

    with patch_cli_exec(help_text=CLAUDE_HELP) as mock_run:
        mock_run.return_value = _completed(envelope)
        with pytest.raises(AIProviderError) as excinfo:
            await provider.complete("Review this")

    message = str(excinfo.value)
    assert_that(message).starts_with("Claude CLI reported error: maximum output")
    assert_that(message).contains('"terminal_reason":"api_error"')
    assert_that(message).contains('"api_error_status":429')


async def test_claude_error_envelope_without_markers_is_unchanged(
    _binaries_on_path: None,
) -> None:
    """A plain error envelope surfaces exactly as before: prose, no suffix."""
    provider = _providers()["anthropic"]
    envelope = json.dumps({"is_error": True, "result": "something broke"})

    with patch_cli_exec(help_text=CLAUDE_HELP) as mock_run:
        mock_run.return_value = _completed(envelope)
        with pytest.raises(AIProviderError) as excinfo:
            await provider.complete("Review this")

    assert_that(str(excinfo.value)).is_equal_to(
        "Claude CLI reported error: something broke",
    )
