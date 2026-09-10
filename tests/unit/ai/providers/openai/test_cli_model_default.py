"""Which model lintro asks ``codex`` for, per credential kind (#2537).

The Codex CLI's two credentials do not accept the same models. A metered API key
reaches the OpenAI API catalogue; a ChatGPT-plan session reaches only that plan's
models and rejects an API-catalogue name outright, failing the whole call with
``The 'gpt-4o' model is not supported when using Codex with a ChatGPT account``.
That is what reddened the openai lane of the Tier 2 contract gate on every run.

The rule these tests pin: an explicit model always wins; with none, an API key
keeps the historical default and a subscription session defers to codex, which
by construction picks a model that session can use.
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 - drives the CLI under test; invocations use shell=False
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.ai.enums import AITransport
from lintro.ai.providers.openai.codex_auth import (
    CODEX_API_KEY_ENV,
    CODEX_HOME_ENV,
    codex_auth_file,
    uses_subscription_session,
)
from lintro.ai.providers.openai.metadata import OPENAI_METADATA
from lintro.ai.providers.openai.provider import (
    CODEX_SESSION_DEFAULT_MODEL,
    DEFAULT_MODEL,
    OpenAIProvider,
)
from tests.unit.ai.conftest import patch_cli_exec

#: A per-call override, distinct from any default so a leak is obvious.
PER_CALL_MODEL = "gpt-5.2-codex"

#: A configured ``ai.model``, likewise distinct.
CONFIGURED_MODEL = "o1-mini"


@pytest.fixture()
def _mock_codex_on_path() -> Iterator[None]:
    """Patch codex binary discovery so no real binary is required."""
    with patch(
        "lintro.ai.providers.openai.provider._find_codex",
        return_value="/usr/local/bin/codex",
    ):
        yield


@pytest.fixture(autouse=True)
def _isolated_codex_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Point codex at an empty home and clear its API key.

    Without this the result would depend on whether the developer running the
    suite happens to be logged into codex, which is exactly the environment
    coupling these tests exist to remove.

    Args:
        tmp_path: Stand-in Codex home.
        monkeypatch: Environment patcher.
    """
    monkeypatch.setenv(CODEX_HOME_ENV, str(tmp_path))
    monkeypatch.delenv(CODEX_API_KEY_ENV, raising=False)


def _write_session(*, api_key: str | None = None) -> None:
    """Write an ``auth.json`` into the isolated Codex home.

    Args:
        api_key: When given, the key an API-key login embeds in the file;
            omitted for a ChatGPT-plan session.
    """
    payload: dict[str, object] = {
        "auth_mode": "chatgpt",
        "tokens": {"account_id": "acct-1"},
    }
    if api_key is not None:
        payload = {"auth_mode": "apikey", "OPENAI_API_KEY": api_key}
    auth_file = codex_auth_file()
    auth_file.parent.mkdir(parents=True, exist_ok=True)
    auth_file.write_text(json.dumps(payload), encoding="utf-8")


def _jsonl_response() -> str:
    """Return a minimal successful codex JSONL envelope.

    Returns:
        Two JSONL events: the agent message and the turn usage.
    """
    return "\n".join(
        [
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "pong"},
                },
            ),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 12, "output_tokens": 3},
                },
            ),
        ],
    )


async def _invoke(*, model: str | None, per_call: str | None) -> list[str]:
    """Run one CLI completion and return the argv codex was called with.

    Args:
        model: Configured ``ai.model``, or None for unset.
        per_call: Per-call model override, or None for unset.

    Returns:
        The command line the transport would have executed.
    """
    provider = OpenAIProvider(model=model, transport=AITransport.CLI)
    with patch_cli_exec() as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=_jsonl_response(),
            stderr="",
        )
        await provider.complete("ping", repo_root="/tmp/repo", model=per_call)
    argv: list[str] = list(mock_run.call_args.args[0])
    return argv


async def test_subscription_session_defers_the_model_to_codex(
    _mock_codex_on_path: None,
) -> None:
    """A plan session with no configured model must not pin one.

    Args:
        _mock_codex_on_path: Binary-discovery patch.
    """
    _write_session()

    argv = await _invoke(model=None, per_call=None)

    assert_that(argv).does_not_contain("--model")
    assert_that(argv).does_not_contain(DEFAULT_MODEL)


async def test_api_key_session_keeps_the_api_catalogue_default(
    _mock_codex_on_path: None,
) -> None:
    """An API key reaches the API catalogue, so the old default still applies.

    Args:
        _mock_codex_on_path: Binary-discovery patch.
    """
    _write_session(api_key="sk-test")

    argv = await _invoke(model=None, per_call=None)

    assert_that(argv).contains("--model", DEFAULT_MODEL)


async def test_api_key_env_alone_keeps_the_api_catalogue_default(
    _mock_codex_on_path: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CODEX_API_KEY beats a stored session, as it does for codex itself.

    Args:
        _mock_codex_on_path: Binary-discovery patch.
        monkeypatch: Environment patcher.
    """
    _write_session()
    monkeypatch.setenv(CODEX_API_KEY_ENV, "sk-test")

    argv = await _invoke(model=None, per_call=None)

    assert_that(argv).contains("--model", DEFAULT_MODEL)


async def test_no_credential_at_all_keeps_the_api_catalogue_default(
    _mock_codex_on_path: None,
) -> None:
    """With nothing stored, behaviour is unchanged from before the fix.

    Args:
        _mock_codex_on_path: Binary-discovery patch.
    """
    argv = await _invoke(model=None, per_call=None)

    assert_that(argv).contains("--model", DEFAULT_MODEL)


@pytest.mark.parametrize("api_key", [None, "sk-test"], ids=["session", "api-key"])
async def test_a_configured_model_wins_under_either_credential(
    _mock_codex_on_path: None,
    api_key: str | None,
) -> None:
    """``ai.model`` is honoured whichever credential is in play.

    Args:
        _mock_codex_on_path: Binary-discovery patch.
        api_key: Embedded API key, or None for a plan session.
    """
    _write_session(api_key=api_key)

    argv = await _invoke(model=CONFIGURED_MODEL, per_call=None)

    assert_that(argv).contains("--model", CONFIGURED_MODEL)


@pytest.mark.parametrize("api_key", [None, "sk-test"], ids=["session", "api-key"])
async def test_a_per_call_model_wins_under_either_credential(
    _mock_codex_on_path: None,
    api_key: str | None,
) -> None:
    """A per-call override beats both the configured model and the defaults.

    Args:
        _mock_codex_on_path: Binary-discovery patch.
        api_key: Embedded API key, or None for a plan session.
    """
    _write_session(api_key=api_key)

    argv = await _invoke(model=CONFIGURED_MODEL, per_call=PER_CALL_MODEL)

    assert_that(argv).contains("--model", PER_CALL_MODEL)
    assert_that(argv).does_not_contain(CONFIGURED_MODEL)


async def test_a_deferred_model_is_reported_as_deferred_not_guessed(
    _mock_codex_on_path: None,
) -> None:
    """The response must not claim a model lintro never asked for.

    codex reports no model in its JSONL, so attributing the answer to the API
    default would put a wrong, priced model into the review's telemetry.

    Args:
        _mock_codex_on_path: Binary-discovery patch.
    """
    _write_session()
    provider = OpenAIProvider(transport=AITransport.CLI)
    with patch_cli_exec() as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=_jsonl_response(),
            stderr="",
        )
        response = await provider.complete("ping", repo_root="/tmp/repo")

    assert_that(response.model).is_equal_to(CODEX_SESSION_DEFAULT_MODEL)
    assert_that(response.model).is_not_equal_to(DEFAULT_MODEL)
    assert_that(response.content).contains("pong")


def test_openai_declares_no_pinned_subscription_model() -> None:
    """The metadata must keep deferring rather than pin a plan slug.

    The plan catalogue is per-account and moves fast, so a pinned slug would
    reintroduce this failure on the next rename for accounts that never had it.
    """
    assert_that(OPENAI_METADATA.cli_default_model).is_none()


def test_a_missing_session_file_is_not_a_subscription() -> None:
    """An absent auth.json must not be read as a plan session."""
    assert_that(uses_subscription_session()).is_false()


def test_an_unreadable_session_file_is_not_a_subscription() -> None:
    """Malformed JSON must fall back rather than raise into the review."""
    auth_file = codex_auth_file()
    auth_file.parent.mkdir(parents=True, exist_ok=True)
    auth_file.write_text("not json", encoding="utf-8")

    assert_that(uses_subscription_session()).is_false()


def test_codex_home_overrides_the_home_relative_location() -> None:
    """CI restores the session outside $HOME, so CODEX_HOME must win.

    A home-relative lookup would read every CI run as unauthenticated and send
    the API-catalogue default straight into the failure this fix removes.
    """
    _write_session()

    assert_that(uses_subscription_session()).is_true()
    assert_that(str(codex_auth_file())).does_not_contain(str(Path.home()))
