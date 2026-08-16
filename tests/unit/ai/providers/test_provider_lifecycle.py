"""Tests for AI provider close/aclose lifecycle (#1885)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from assertpy import assert_that

from lintro.ai.json_response import CliSchemaRequest
from lintro.ai.providers.base import AIResponse, BaseAIProvider


class _FakeAsyncClient:
    """Minimal stand-in for an async SDK client with ``close``."""

    def __init__(self) -> None:
        self.closed = False
        self.close_calls = 0

    async def close(self) -> None:
        """Mark the client closed."""
        self.closed = True
        self.close_calls += 1


class _LifecycleProvider(BaseAIProvider):
    """Concrete provider that returns a fake async SDK client."""

    def __init__(self) -> None:
        super().__init__(
            provider_name="lifecycle",
            has_sdk=True,
            sdk_package="lifecycle",
            default_model="lifecycle-model",
            default_api_key_env="LIFECYCLE_TEST_KEY",
        )
        self.created_clients: list[_FakeAsyncClient] = []

    def _create_client(self, *, api_key: str) -> _FakeAsyncClient:
        del api_key
        client = _FakeAsyncClient()
        self.created_clients.append(client)
        return client

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        timeout: float = 60.0,
        repo_root: str | None = None,
        use_one_shot: bool = False,
        model: str | None = None,
        cli_schema: CliSchemaRequest | None = None,
    ) -> AIResponse:
        """Return a canned response."""
        del (
            prompt,
            system,
            max_tokens,
            timeout,
            repo_root,
            use_one_shot,
            model,
            cli_schema,
        )
        return AIResponse(content="ok", model="lifecycle-model")


async def test_base_aclose_is_noop_without_client() -> None:
    """Base aclose is safe when no client was ever created."""
    provider = _LifecycleProvider()
    await provider.aclose()
    await provider.aclose()
    assert_that(provider._client).is_none()
    assert_that(provider._superseded_clients).is_empty()


async def test_aclose_closes_current_sdk_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Aclose closes the active SDK client and clears the cache."""
    monkeypatch.setenv("LIFECYCLE_TEST_KEY", "test-key")
    provider = _LifecycleProvider()
    client = provider._get_client()
    assert_that(client.closed).is_false()

    await provider.aclose()

    assert_that(client.closed).is_true()
    assert_that(provider._client).is_none()
    assert_that(provider._client_loop).is_none()


async def test_aclose_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Double aclose does not raise and closes the client once."""
    monkeypatch.setenv("LIFECYCLE_TEST_KEY", "test-key")
    provider = _LifecycleProvider()
    client = provider._get_client()

    await provider.aclose()
    await provider.aclose()

    assert_that(client.closed).is_true()
    assert_that(client.close_calls).is_equal_to(1)


async def test_stale_loop_rebuild_does_not_orphan_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A loop-stale rebuild retires the old client; aclose closes it."""
    monkeypatch.setenv("LIFECYCLE_TEST_KEY", "test-key")
    provider = _LifecycleProvider()

    # Real loops so aclose() exercises the genuine dispatch (is_running / is
    # current), not an AttributeError swallowed by best-effort teardown.
    loop_a = asyncio.new_event_loop()
    loop_b = asyncio.get_running_loop()
    try:
        monkeypatch.setattr(
            BaseAIProvider,
            "_current_loop",
            staticmethod(lambda: loop_a),
        )
        first = provider._get_client()

        monkeypatch.setattr(
            BaseAIProvider,
            "_current_loop",
            staticmethod(lambda: loop_b),
        )
        second = provider._get_client()
    finally:
        loop_a.close()

    assert_that(first).is_not_equal_to(second)
    assert_that(
        [client for client, _loop in provider._superseded_clients],
    ).contains(first)
    assert_that(first.closed).is_false()

    await provider.aclose()

    # A client from an idle foreign loop is consciously released, never
    # awaited on the wrong loop; only the current-loop client closes.
    assert_that(first.closed).is_false()
    assert_that(second.closed).is_true()
    assert_that(provider._superseded_clients).is_empty()


async def test_anthropic_aclose_closes_sdk_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AnthropicProvider.aclose closes the AsyncAnthropic-like client."""
    pytest.importorskip("anthropic")
    from lintro.ai.providers.anthropic import AnthropicProvider

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    provider = AnthropicProvider()
    # ``spec`` keeps MagicMock from inventing an ``aclose`` attr that would
    # shadow the real SDK's async ``close()``.
    fake = MagicMock(spec=["close"])
    fake.close = AsyncMock()
    monkeypatch.setattr(provider, "_create_client", lambda *, api_key: fake)
    assert_that(provider._get_client()).is_equal_to(fake)

    await provider.aclose()
    await provider.aclose()

    fake.close.assert_awaited_once()
    assert_that(provider._client).is_none()


async def test_openai_aclose_closes_sdk_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenAIProvider.aclose closes the AsyncOpenAI-like client."""
    pytest.importorskip("openai")
    from lintro.ai.providers.openai import OpenAIProvider

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    provider = OpenAIProvider()
    fake = MagicMock(spec=["close"])
    fake.close = AsyncMock()
    monkeypatch.setattr(provider, "_create_client", lambda *, api_key: fake)
    assert_that(provider._get_client()).is_equal_to(fake)

    await provider.aclose()
    await provider.aclose()

    fake.close.assert_awaited_once()
    assert_that(provider._client).is_none()


async def test_cursor_aclose_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """CursorProvider.aclose is a no-op (no poolable client)."""
    import lintro.ai.providers.cursor as cursor_mod
    from lintro.ai.providers.cursor import CursorProvider

    monkeypatch.setattr(cursor_mod, "_find_agent", lambda: "/usr/bin/agent")
    provider = CursorProvider(cursor_trust_workspace=False)
    await provider.aclose()
    await provider.aclose()
    assert_that(provider._client).is_none()


def test_sync_close_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    """close() runs aclose() outside of an event loop."""
    monkeypatch.setenv("LIFECYCLE_TEST_KEY", "test-key")
    provider = _LifecycleProvider()
    client = _FakeAsyncClient()
    provider._client = client
    # No creating loop recorded: aclose treats the client as current-loop
    # owned and awaits its close directly.
    provider._client_loop = None

    provider.close()

    assert_that(client.closed).is_true()
    assert_that(provider._client).is_none()


class _ExplodingClient:
    """SDK-client stand-in whose ``close`` always raises."""

    async def close(self) -> None:
        """Raise to simulate a failing pool teardown."""
        raise RuntimeError("pool teardown failed")


async def test_aclose_survives_failing_client_and_closes_the_rest() -> None:
    """One client's failing close must not orphan the remaining clients."""
    provider = _LifecycleProvider()
    survivor = _FakeAsyncClient()
    provider._superseded_clients = [
        (_ExplodingClient(), None),
        (survivor, None),
    ]
    provider._client = None

    await provider.aclose()

    assert_that(survivor.closed).is_true()
    assert_that(provider._superseded_clients).is_empty()


async def test_aclose_skips_client_whose_creating_loop_is_closed() -> None:
    """A client from a dead loop is skipped, not awaited on the wrong loop.

    Async pools must close on their creating loop; when that loop is gone the
    pool is left to garbage collection and the remaining clients still close.
    """
    dead_loop = asyncio.new_event_loop()
    dead_loop.close()
    stranded = _FakeAsyncClient()
    survivor = _FakeAsyncClient()
    provider = _LifecycleProvider()
    provider._superseded_clients = [(stranded, dead_loop), (survivor, None)]

    await provider.aclose()

    assert_that(stranded.closed).is_false()
    assert_that(survivor.closed).is_true()
    assert_that(provider._superseded_clients).is_empty()


class _FakeAcloseClient:
    """SDK-client stand-in exposing ``aclose`` (the preferred spelling)."""

    def __init__(self) -> None:
        self.aclosed = False

    async def aclose(self) -> None:
        """Mark the client aclosed."""
        self.aclosed = True


async def test_close_sdk_client_prefers_aclose() -> None:
    """``aclose`` is used when both spellings exist (httpx-style clients)."""
    client = _FakeAcloseClient()
    provider = _LifecycleProvider()
    provider._client = client

    await provider.aclose()

    assert_that(client.aclosed).is_true()


async def test_sync_close_raises_inside_running_loop() -> None:
    """close() refuses to run inside an event loop and points at aclose()."""
    provider = _LifecycleProvider()

    with pytest.raises(RuntimeError) as excinfo:
        provider.close()

    assert_that(str(excinfo.value)).contains("await aclose()")


async def test_aclose_closes_client_on_live_foreign_loop() -> None:
    """A client whose creating loop runs in another thread closes there.

    Exercises the ``run_coroutine_threadsafe`` branch: the close coroutine
    must execute on the foreign loop's thread, not the caller's.
    """
    import threading

    foreign_loop = asyncio.new_event_loop()
    started = threading.Event()

    def _run() -> None:
        foreign_loop.call_soon(started.set)
        foreign_loop.run_forever()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    # aclose() dispatches on is_running(); wait until the loop actually
    # runs so the test cannot flake into the release branch.
    assert_that(started.wait(timeout=5.0)).is_true()
    try:

        class _LoopRecordingClient:
            def __init__(self) -> None:
                self.closed_on: asyncio.AbstractEventLoop | None = None

            async def close(self) -> None:
                self.closed_on = asyncio.get_running_loop()

        client = _LoopRecordingClient()
        provider = _LifecycleProvider()
        provider._superseded_clients = [(client, foreign_loop)]

        await provider.aclose()

        assert_that(client.closed_on).is_same_as(foreign_loop)
        assert_that(provider._superseded_clients).is_empty()
    finally:
        foreign_loop.call_soon_threadsafe(foreign_loop.stop)
        thread.join(timeout=5.0)
        foreign_loop.close()
