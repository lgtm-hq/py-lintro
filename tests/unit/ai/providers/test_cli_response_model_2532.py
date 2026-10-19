"""CLI transports label and price a response with the model that answered (#2532).

``AIResponse.model`` is documented as the model that produced the response.
The CLI transports built it from the model captured at construction, so a
per-call override ran as ``--model override`` and came back labelled, and on
codex priced, as the configured model. The model is now part of
``parse_stdout``'s contract and the response is stamped and priced with it.
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 - only CompletedProcess objects are constructed here
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.ai.cost import estimate_cost
from lintro.ai.enums import AITransport, CliBareMode
from lintro.ai.providers.anthropic.provider import AnthropicProvider
from lintro.ai.providers.base import BaseAIProvider
from lintro.ai.providers.cursor.provider import CursorProvider
from lintro.ai.providers.openai.provider import OpenAIProvider
from tests.unit.ai.conftest import CLAUDE_HELP, patch_cli_exec

#: Two models the metadata prices differently, per provider, so a response
#: priced at the wrong one is a different number.
_CONFIGURED = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4o",
    "cursor": "auto",
}
_OVERRIDE = {
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-4o-mini",
    "cursor": "sonnet-4",
}

_INPUT = 1_000_000
_OUTPUT = 1_000_000


def _claude_stdout(*, cost: float | None) -> str:
    """Return a ``claude --output-format json`` envelope.

    Args:
        cost: ``total_cost_usd`` to report, or ``None`` to omit it so the
            transport has to price the call itself.

    Returns:
        The envelope as text.
    """
    payload: dict[str, object] = {
        "result": "ok",
        "usage": {"input_tokens": _INPUT, "output_tokens": _OUTPUT},
    }
    if cost is not None:
        payload["total_cost_usd"] = cost
    return json.dumps(payload)


_CODEX_STDOUT = "\n".join(
    [
        json.dumps(
            {"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}},
        ),
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {"input_tokens": _INPUT, "output_tokens": _OUTPUT},
            },
        ),
    ],
)

_CURSOR_STDOUT = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "ok",
        "session_id": "sess-1",
        "usage": {"inputTokens": _INPUT, "outputTokens": _OUTPUT},
    },
)


@pytest.fixture()
def _binaries_on_path() -> Iterator[None]:
    """Patch every CLI binary lookup so each provider constructs."""
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


def _provider(name: str) -> BaseAIProvider:
    """Return one CLI-transport provider configured with its default model.

    Args:
        name: ``anthropic``, ``cursor`` or ``openai``.

    Returns:
        The provider instance.
    """
    model = _CONFIGURED[name]
    if name == "anthropic":
        return AnthropicProvider(
            model=model,
            transport=AITransport.CLI,
            cli_bare=CliBareMode.NEVER,
        )
    if name == "cursor":
        return CursorProvider(
            model=model,
            transport=AITransport.CLI,
            cursor_trust_workspace=True,
        )
    return OpenAIProvider(model=model, transport=AITransport.CLI)


def _stdout(name: str) -> str:
    """Return a successful envelope without a reported cost for *name*.

    Args:
        name: ``anthropic``, ``cursor`` or ``openai``.

    Returns:
        The CLI stdout to replay.
    """
    if name == "anthropic":
        return _claude_stdout(cost=None)
    if name == "cursor":
        return _CURSOR_STDOUT
    return _CODEX_STDOUT


def _completed(stdout: str) -> subprocess.CompletedProcess[str]:
    """Return a clean-exit completed process carrying *stdout*.

    Args:
        stdout: Standard output text.

    Returns:
        A zero-exit completed process.
    """
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


@pytest.mark.usefixtures("_binaries_on_path")
@pytest.mark.parametrize("name", ["anthropic", "openai", "cursor"])
async def test_a_per_call_override_is_the_model_on_the_response(name: str) -> None:
    """``stream_complete(model=...)`` under CLI transport names the override."""
    provider = _provider(name)
    with patch_cli_exec(help_text=CLAUDE_HELP) as mock_run:
        mock_run.return_value = _completed(_stdout(name))
        result = await provider.stream_complete("hi", model=_OVERRIDE[name])
        response = await result.collect()

    cmd = mock_run.transport_calls[-1].cmd
    assert_that(cmd[cmd.index("--model") + 1]).is_equal_to(_OVERRIDE[name])
    assert_that(response.model).is_equal_to(_OVERRIDE[name])


@pytest.mark.usefixtures("_binaries_on_path")
@pytest.mark.parametrize("name", ["anthropic", "openai", "cursor"])
async def test_without_an_override_the_response_names_the_configured_model(
    name: str,
) -> None:
    """The configured model is still what a plain call reports."""
    provider = _provider(name)
    with patch_cli_exec(help_text=CLAUDE_HELP) as mock_run:
        mock_run.return_value = _completed(_stdout(name))
        response = await provider.complete("hi")

    assert_that(response.model).is_equal_to(_CONFIGURED[name])


@pytest.mark.usefixtures("_binaries_on_path")
@pytest.mark.parametrize("name", ["anthropic", "openai"])
async def test_an_unpriced_call_is_priced_at_the_override_rate(name: str) -> None:
    """When the envelope reports no cost, the override's rates apply, not the default's."""
    provider = _provider(name)
    with patch_cli_exec(help_text=CLAUDE_HELP) as mock_run:
        mock_run.return_value = _completed(_stdout(name))
        response = await provider.complete("hi", model=_OVERRIDE[name])

    expected = estimate_cost(_OVERRIDE[name], _INPUT, _OUTPUT)
    configured = estimate_cost(_CONFIGURED[name], _INPUT, _OUTPUT)
    assert_that(expected).is_not_equal_to(configured)
    assert_that(response.cost_estimate).is_equal_to(expected)
    assert_that(response.cost_reported).is_false()


@pytest.mark.usefixtures("_binaries_on_path")
async def test_a_reported_cost_survives_an_override_unchanged() -> None:
    """The CLI's own ``total_cost_usd`` is authoritative whatever model ran."""
    provider = _provider("anthropic")
    with patch_cli_exec(help_text=CLAUDE_HELP) as mock_run:
        mock_run.return_value = _completed(_claude_stdout(cost=0.42))
        response = await provider.complete("hi", model=_OVERRIDE["anthropic"])

    assert_that(response.model).is_equal_to(_OVERRIDE["anthropic"])
    assert_that(response.cost_estimate).is_equal_to(0.42)
    assert_that(response.cost_reported).is_true()


@pytest.mark.usefixtures("_binaries_on_path")
async def test_a_prose_answer_is_labelled_with_the_override_too() -> None:
    """The prose-recovery path stamps the same model as the envelope path."""
    provider = _provider("anthropic")
    with patch_cli_exec(help_text=CLAUDE_HELP) as mock_run:
        mock_run.return_value = _completed("Findings follow.")
        response = await provider.complete("hi", model=_OVERRIDE["anthropic"])

    assert_that(response.content).is_equal_to("Findings follow.")
    assert_that(response.model).is_equal_to(_OVERRIDE["anthropic"])
