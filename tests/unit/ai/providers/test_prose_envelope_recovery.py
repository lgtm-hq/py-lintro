"""CLI transports recover prose envelopes and never truncate evidence (#1853).

A ``claude``/``agent``/``codex`` invocation that exits zero but answers in prose
used to be rejected wholesale, with the evidence cut to ``stdout[:500]``. These
tests pin the replacement: the prose comes back as unstructured content, and any
genuinely unrecoverable output is reported in full and written to disk.
"""

from __future__ import annotations

import subprocess  # nosec B404 - subprocess types drive the CLI doubles under test
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.ai.enums import AITransport, CliBareMode
from lintro.ai.exceptions import AIProviderError
from lintro.ai.providers.anthropic import AnthropicProvider
from lintro.ai.providers.cursor import CursorProvider
from lintro.ai.providers.openai import OpenAIProvider
from lintro.ai.raw_response import RAW_RESPONSE_DIR
from tests.unit.ai.conftest import patch_cli_exec

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
            "lintro.ai.providers.anthropic._find_claude",
            return_value="/usr/local/bin/claude",
        ),
        patch(
            "lintro.ai.providers.cursor._find_agent",
            return_value="/usr/local/bin/agent",
        ),
        patch(
            "lintro.ai.providers.openai._find_codex",
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


def _providers() -> dict[str, object]:
    """Return one CLI-transport provider per implementation.

    Returns:
        Mapping of provider label to provider instance.
    """
    return {
        "anthropic": AnthropicProvider(
            transport=AITransport.CLI,
            cli_bare=CliBareMode.NEVER,
        ),
        "cursor": CursorProvider(transport=AITransport.CLI),
        "openai": OpenAIProvider(transport=AITransport.CLI),
    }


@pytest.mark.parametrize("name", ["anthropic", "cursor", "openai"])
async def test_prose_envelope_is_recovered_in_full(
    name: str,
    _binaries_on_path: None,
) -> None:
    """Prose stdout becomes unstructured content instead of an error."""
    provider = _providers()[name]

    with patch_cli_exec() as mock_run:
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

    with patch_cli_exec() as mock_run:
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
    with patch_cli_exec() as mock_run:
        mock_run.return_value = _completed("   \n\t ")
        with pytest.raises(AIProviderError) as excinfo:
            await provider.complete("Review this")

    assert_that(str(excinfo.value)).contains("Full raw output")
    assert_that(str(excinfo.value)).does_not_contain("Raw output:")


@pytest.mark.parametrize("name", ["anthropic", "cursor", "openai"])
async def test_error_evidence_is_never_truncated_to_500_chars(
    name: str,
    _binaries_on_path: None,
) -> None:
    """The historical ``stdout[:500]`` cap is gone from every provider."""
    provider = _providers()[name]

    with patch_cli_exec() as mock_run:
        mock_run.return_value = _completed(_PROSE)
        response = await provider.complete("Review this")

    assert_that(len(response.content)).is_greater_than(500)
