"""Tests for Cursor's ``transport: api`` through ``cursor-sdk`` (#3253).

The real SDK is never imported here: a recording stand-in is injected as
``sys.modules["cursor_sdk"]``. It appends every bridge launch and prompt to
plain lists, so the assertions read what the provider actually sent rather
than mock call bookkeeping (#2315).
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from assertpy import assert_that

from lintro.ai.cost import estimate_cost_with_floor
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import (
    AIAuthenticationError,
    AINotAvailableError,
    AIProviderError,
    AIRateLimitError,
)
from lintro.ai.liveness import LivenessState
from lintro.ai.providers.cursor import provider as provider_mod
from lintro.ai.providers.cursor import sdk_transport
from lintro.ai.providers.cursor.provider import CursorProvider
from lintro.ai.providers.cursor.sdk_transport import (
    CURSOR_API_MIN_TIMEOUT,
    CURSOR_DISALLOWED_TOOLS,
    api_timeout,
    sdk_agent_options,
)
from lintro.ai.token_budget import estimate_tokens

_KEY_ENV = "CURSOR_API_KEY"


class _CursorAgentError(Exception):
    """Stand-in for the SDK's base error."""

    def __init__(self, message: str, *, retry_after: str | None = None) -> None:
        """Record the message and the advertised retry delay.

        Args:
            message: Error text.
            retry_after: The SDK's string ``retry_after``, if any.
        """
        super().__init__(message)
        self.retry_after = retry_after


class _AuthenticationError(_CursorAgentError):
    """Stand-in for ``cursor_sdk.AuthenticationError``."""


class _RateLimitError(_CursorAgentError):
    """Stand-in for ``cursor_sdk.RateLimitError``."""


class _ConfigurationError(_CursorAgentError):
    """Stand-in for ``cursor_sdk.ConfigurationError``."""


class _BadRequestError(_CursorAgentError):
    """Stand-in for ``cursor_sdk.BadRequestError``."""


class _Options:
    """Stand-in for an SDK options dataclass that keeps the kwargs it got."""

    def __init__(self, **kwargs: Any) -> None:
        """Store every keyword argument as given and as attributes.

        Args:
            **kwargs: The option fields lintro passed.
        """
        self.kwargs = kwargs
        for name, value in kwargs.items():
            setattr(self, name, value)


@dataclass
class _FakeClient:
    """A bridge client that records whether it was closed."""

    workspace: str
    closed: bool = False

    async def aclose(self) -> None:
        """Mark the client closed."""
        self.closed = True


@dataclass
class _FakeSdk:
    """Recording state behind the injected ``cursor_sdk`` module.

    Attributes:
        result: What ``AsyncAgent.prompt`` returns.
        raises: Raised by ``AsyncAgent.prompt`` instead, when set.
        delay: Seconds ``AsyncAgent.prompt`` sleeps first.
        clients: Every client ``launch_bridge`` returned, in order.
        prompts: ``(message, options, client)`` for each prompt, in order.
    """

    result: Any = None
    raises: Exception | None = None
    delay: float = 0.0
    clients: list[_FakeClient] = field(default_factory=list)
    prompts: list[tuple[str, Any, Any]] = field(default_factory=list)

    def module(self) -> ModuleType:
        """Build the module object the provider will import.

        Returns:
            A ``cursor_sdk`` stand-in wired to this recorder.
        """
        state = self

        class AsyncClient:
            @staticmethod
            async def launch_bridge(*, workspace: str) -> _FakeClient:
                client = _FakeClient(workspace=workspace)
                state.clients.append(client)
                return client

        class AsyncAgent:
            @staticmethod
            async def prompt(message: str, options: Any, *, client: Any) -> Any:
                state.prompts.append((message, options, client))
                if state.delay:
                    await asyncio.sleep(state.delay)
                if state.raises is not None:
                    raise state.raises
                return state.result

        module = ModuleType("cursor_sdk")
        module.AsyncClient = AsyncClient  # type: ignore[attr-defined]
        module.AsyncAgent = AsyncAgent  # type: ignore[attr-defined]
        module.AgentOptions = _Options  # type: ignore[attr-defined]
        module.LocalAgentOptions = _Options  # type: ignore[attr-defined]
        module.CursorAgentError = _CursorAgentError  # type: ignore[attr-defined]
        module.AuthenticationError = _AuthenticationError  # type: ignore[attr-defined]
        module.RateLimitError = _RateLimitError  # type: ignore[attr-defined]
        module.ConfigurationError = _ConfigurationError  # type: ignore[attr-defined]
        return module


def _run_result(
    *,
    status: str = "finished",
    result: str = '{"findings": []}',
    input_tokens: int | None = 120,
    output_tokens: int | None = 30,
    model: str | None = "composer-2",
) -> SimpleNamespace:
    """Build a stand-in ``RunResult``.

    Args:
        status: Run status.
        result: Result text.
        input_tokens: Reported input tokens; ``None`` for no usage at all.
        output_tokens: Reported output tokens.
        model: The model the run names.

    Returns:
        The result stand-in.
    """
    usage = (
        None
        if input_tokens is None
        else SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=999,
            cache_write_tokens=999,
            total_tokens=0,
        )
    )
    return SimpleNamespace(
        status=status,
        result=result,
        usage=usage,
        model=model,
        error_code=None,
        error_message=None,
    )


@pytest.fixture()
def fake_sdk(monkeypatch: pytest.MonkeyPatch) -> Iterator[_FakeSdk]:
    """Install the recording SDK, mark it present and set the API key.

    Args:
        monkeypatch: Pytest attribute patcher.

    Yields:
        _FakeSdk: The recorder.
    """
    sdk = _FakeSdk(result=_run_result())
    monkeypatch.setitem(sys.modules, "cursor_sdk", sdk.module())
    monkeypatch.setattr(provider_mod, "_has_cursor_sdk", True)
    monkeypatch.setattr(provider_mod, "_find_agent", lambda: None)
    monkeypatch.setenv(_KEY_ENV, "test-key")
    yield sdk


def _api_provider(**kwargs: Any) -> CursorProvider:
    """Build Cursor on the API transport.

    Args:
        **kwargs: Extra constructor arguments.

    Returns:
        The provider.
    """
    return CursorProvider(
        transport=AITransport.API,
        cursor_trust_workspace=True,
        **kwargs,
    )


def _complete(provider: CursorProvider, **kwargs: Any) -> Any:
    """Run one completion, then close the provider, on a fresh loop.

    Args:
        provider: The provider.
        **kwargs: Arguments for ``complete``.

    Returns:
        The response.
    """

    async def _go() -> Any:
        return await provider.complete("review this diff", **kwargs)

    return asyncio.run(_go())


# -- Pure option builder ----------------------------------------------------


def test_sdk_agent_options_grant_no_tool_and_no_cloud(tmp_path: Path) -> None:
    """The options lintro hands the SDK are text-only and local (read-only pin).

    Args:
        tmp_path: Stand-in working directory.
    """
    options = sdk_agent_options(model="auto", cwd=str(tmp_path), api_key="k")

    assert_that(options["tools"]).is_equal_to([])
    assert_that({"shell", "edit", "mcp", "task", "webSearch"}).is_subset_of(
        set(options["disallowed_tools"]),
    )
    assert_that(options["disallowed_tools"]).is_equal_to(
        list(CURSOR_DISALLOWED_TOOLS),
    )
    assert_that(options).does_not_contain_key("cloud")
    assert_that(options["mode"]).is_equal_to("agent")
    assert_that(options["local"]).is_equal_to({"cwd": str(tmp_path)})


@pytest.mark.parametrize(
    ("requested", "expected"),
    [(60.0, CURSOR_API_MIN_TIMEOUT), (900.0, 900.0)],
)
def test_api_timeout_floors_the_configured_value(
    requested: float,
    expected: float,
) -> None:
    """``ai.transports.api.timeout``'s 60 s default is raised to 180 s.

    Args:
        requested: Caller's timeout.
        expected: Timeout the prompt gets.
    """
    assert_that(api_timeout(requested)).is_equal_to(expected)


# -- Construction -----------------------------------------------------------


def test_api_construction_touches_neither_sdk_nor_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Building the provider imports nothing, starts nothing, reads no key.

    ``sys.modules["cursor_sdk"] = None`` makes any import of it raise, so a
    construction that imported the SDK would fail here.

    Args:
        monkeypatch: Pytest attribute patcher.
    """
    monkeypatch.setitem(sys.modules, "cursor_sdk", None)
    monkeypatch.setattr(provider_mod, "_has_cursor_sdk", True)
    monkeypatch.setattr(provider_mod, "_find_agent", lambda: None)
    monkeypatch.delenv(_KEY_ENV, raising=False)

    provider = _api_provider()

    assert_that(provider._transport).is_equal_to(AITransport.API)
    assert_that(provider._sdk_bridge).is_not_none()
    assert_that(provider._sdk_bridge.workdir if provider._sdk_bridge else "x").is_none()
    assert_that(provider._cli_transport()).is_none()


def test_api_transport_without_the_sdk_names_the_cursor_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing SDK is rejected with the ``lintro[cursor]`` hint.

    Args:
        monkeypatch: Pytest attribute patcher.
    """
    monkeypatch.setattr(provider_mod, "_has_cursor_sdk", False)

    with pytest.raises(AINotAvailableError) as excinfo:
        _api_provider()

    assert_that(str(excinfo.value)).contains("'cursor-sdk'")
    assert_that(str(excinfo.value)).contains("lintro[cursor]")


@pytest.mark.usefixtures("fake_sdk")
def test_api_transport_rejects_a_base_url() -> None:
    """Cursor has no endpoint override; ``ai.api_base_url`` is an error."""
    with pytest.raises(AINotAvailableError) as excinfo:
        _api_provider(base_url="https://gateway.example")

    assert_that(str(excinfo.value)).is_equal_to(
        "cursor API transport does not accept ai.api_base_url",
    )


@pytest.mark.usefixtures("fake_sdk")
def test_api_capabilities_declare_no_session() -> None:
    """The SDK path is stateless: no session, no structured output or stream."""
    provider = _api_provider()

    provider.begin_durable_session(repo_root="/repo")

    assert_that(provider.capabilities.supports_sessions).is_false()
    assert_that(provider.capabilities.supports_structured_output).is_false()
    assert_that(provider.capabilities.supports_streaming).is_false()
    assert_that(provider._durable_repo_root).is_none()


def test_api_availability_follows_the_key(
    fake_sdk: _FakeSdk,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under API, availability is SDK + key; the ``agent`` binary is irrelevant.

    Args:
        fake_sdk: The recording SDK.
        monkeypatch: Pytest attribute patcher.
    """
    provider = _api_provider()
    with_key = provider.is_available()
    monkeypatch.delenv(_KEY_ENV)

    assert_that(with_key).is_true()
    assert_that(provider.is_available()).is_false()
    assert_that(fake_sdk.clients).is_empty()


# -- A call -----------------------------------------------------------------


def test_api_call_runs_tool_less_in_an_empty_provider_tempdir(
    fake_sdk: _FakeSdk,
    tmp_path: Path,
) -> None:
    """The agent's cwd is lintro's empty tempdir, never the repo or the CWD.

    Args:
        fake_sdk: The recording SDK.
        tmp_path: Stand-in repository under review.
    """
    provider = _api_provider()

    async def _go() -> str | None:
        await provider.complete("review", repo_root=str(tmp_path))
        bridge = provider._sdk_bridge
        workdir = bridge.workdir if bridge else None
        assert_that(os.listdir(workdir or "")).is_empty()
        return workdir

    workdir = asyncio.run(_go())
    _message, options, client = fake_sdk.prompts[0]

    assert_that(options.tools).is_equal_to([])
    assert_that({"shell", "edit", "mcp", "task", "webSearch"}).is_subset_of(
        set(options.disallowed_tools),
    )
    assert_that(options.kwargs).does_not_contain_key("cloud")
    assert_that(options.local.kwargs).is_equal_to({"cwd": workdir})
    assert_that(options.local.cwd).is_not_equal_to(str(tmp_path))
    assert_that(options.local.cwd).is_not_equal_to(os.getcwd())
    assert_that(options.api_key).is_equal_to("test-key")
    assert_that(client.workspace).is_equal_to(workdir)


def test_api_call_states_the_output_cap_and_folds_the_system_prompt(
    fake_sdk: _FakeSdk,
) -> None:
    """The SDK has no ``max_tokens``, so the cap is stated in the prompt.

    Args:
        fake_sdk: The recording SDK.
    """
    _complete(_api_provider(max_tokens=500), system="be terse", max_tokens=2000)

    message = fake_sdk.prompts[0][0]
    assert_that(message).starts_with("be terse\n\n---\n\nreview this diff")
    assert_that(message).ends_with("[Respond in at most 500 tokens.]")


def test_api_call_records_reported_usage_and_the_floor_cost(
    fake_sdk: _FakeSdk,
) -> None:
    """Input/output tokens come from the run; cache tokens are not added.

    Args:
        fake_sdk: The recording SDK.
    """
    response = _complete(_api_provider())

    assert_that(response.content).is_equal_to('{"findings": []}')
    assert_that(response.input_tokens).is_equal_to(120)
    assert_that(response.output_tokens).is_equal_to(30)
    assert_that(response.cost_estimate).is_equal_to(
        estimate_cost_with_floor("auto", 120, 30),
    )
    assert_that(response.cost_estimate).is_greater_than(0.0)
    assert_that([m.model for m in response.reported_models]).is_equal_to(
        ["composer-2"],
    )
    assert_that(fake_sdk.prompts).is_length(1)


def test_api_call_estimates_tokens_when_the_run_reports_none(
    fake_sdk: _FakeSdk,
) -> None:
    """A run without usage falls back to local estimates, not zero.

    Args:
        fake_sdk: The recording SDK.
    """
    fake_sdk.result = _run_result(input_tokens=None, result="short answer")

    response = _complete(_api_provider())

    assert_that(response.input_tokens).is_equal_to(
        estimate_tokens(fake_sdk.prompts[0][0]),
    )
    assert_that(response.output_tokens).is_equal_to(estimate_tokens("short answer"))


def test_api_call_without_a_key_fails_before_starting_a_bridge(
    fake_sdk: _FakeSdk,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No key is an authentication error, and no bridge process is spawned.

    Args:
        fake_sdk: The recording SDK.
        monkeypatch: Pytest attribute patcher.
    """
    monkeypatch.delenv(_KEY_ENV)

    with pytest.raises(AIAuthenticationError) as excinfo:
        _complete(_api_provider())

    assert_that(str(excinfo.value)).contains(_KEY_ENV)
    assert_that(fake_sdk.clients).is_empty()


@pytest.mark.parametrize("status", ["error", "cancelled", "expired"])
def test_an_unfinished_run_is_a_provider_error(
    fake_sdk: _FakeSdk,
    status: str,
) -> None:
    """Only ``finished`` yields a response.

    Args:
        fake_sdk: The recording SDK.
        status: The run's terminal status.
    """
    fake_sdk.result = _run_result(status=status)

    with pytest.raises(AIProviderError) as excinfo:
        _complete(_api_provider())

    assert_that(str(excinfo.value)).is_equal_to(f"Cursor SDK run {status}")


def test_an_unfinished_run_carries_the_sdk_error_message(fake_sdk: _FakeSdk) -> None:
    """``RunResult.error_message`` (present in 1.3.1) is surfaced when set.

    Args:
        fake_sdk: The recording SDK.
    """
    result = _run_result(status="error")
    result.error_message = "model unavailable"
    fake_sdk.result = result

    with pytest.raises(AIProviderError) as excinfo:
        _complete(_api_provider())

    assert_that(str(excinfo.value)).is_equal_to(
        "Cursor SDK run error: model unavailable",
    )


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (_AuthenticationError("bad key"), AIAuthenticationError),
        (_ConfigurationError("no model"), AINotAvailableError),
        (_BadRequestError("bad"), AIProviderError),
        (_CursorAgentError("boom"), AIProviderError),
    ],
)
def test_sdk_errors_map_onto_lintro_errors(
    fake_sdk: _FakeSdk,
    raised: Exception,
    expected: type[Exception],
) -> None:
    """Each SDK error class surfaces as the matching lintro error.

    Args:
        fake_sdk: The recording SDK.
        raised: What the SDK raises.
        expected: The lintro error type the caller sees.
    """
    fake_sdk.raises = raised

    with pytest.raises(expected) as excinfo:
        _complete(_api_provider())

    assert_that(type(excinfo.value)).is_same_as(expected)
    assert_that(excinfo.value.__cause__).is_same_as(raised)


@pytest.mark.parametrize(("retry_after", "seconds"), [("7", 7.0), (None, None)])
def test_rate_limit_carries_the_sdk_retry_after(
    fake_sdk: _FakeSdk,
    retry_after: str | None,
    seconds: float | None,
) -> None:
    """The SDK's string ``retry_after`` becomes seconds for ``with_retry``.

    Args:
        fake_sdk: The recording SDK.
        retry_after: The SDK's advertised delay.
        seconds: The parsed delay.
    """
    fake_sdk.raises = _RateLimitError("slow down", retry_after=retry_after)

    with pytest.raises(AIRateLimitError) as excinfo:
        _complete(_api_provider())

    assert_that(excinfo.value.retry_after).is_equal_to(seconds)


def test_a_run_past_the_timeout_is_a_provider_error(
    fake_sdk: _FakeSdk,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``asyncio.wait_for`` bounds the prompt.

    Args:
        fake_sdk: The recording SDK.
        monkeypatch: Pytest attribute patcher.
    """
    monkeypatch.setattr(sdk_transport, "CURSOR_API_MIN_TIMEOUT", 0.01)
    fake_sdk.delay = 5.0

    with pytest.raises(AIProviderError) as excinfo:
        _complete(_api_provider(), timeout=0.01)

    assert_that(str(excinfo.value)).starts_with("Cursor SDK run timed out")


# -- Bridge lifecycle ---------------------------------------------------------


def test_one_bridge_serves_every_call_on_a_loop(fake_sdk: _FakeSdk) -> None:
    """Calls share the bridge the first one started.

    Args:
        fake_sdk: The recording SDK.
    """
    provider = _api_provider()

    async def _go() -> None:
        await asyncio.gather(
            provider.complete("a"),
            provider.complete("b"),
            provider.complete("c"),
        )

    asyncio.run(_go())

    assert_that(fake_sdk.clients).is_length(1)
    assert_that({id(client) for _m, _o, client in fake_sdk.prompts}).is_length(1)


def test_a_new_loop_gets_a_new_bridge(fake_sdk: _FakeSdk) -> None:
    """A bridge client is bound to its loop; another loop starts another.

    Args:
        fake_sdk: The recording SDK.
    """
    provider = _api_provider()

    _complete(provider)
    _complete(provider)

    assert_that(fake_sdk.clients).is_length(2)
    assert_that(fake_sdk.prompts[1][2]).is_same_as(fake_sdk.clients[1])


def test_aclose_closes_the_bridge_and_removes_the_workdir(
    fake_sdk: _FakeSdk,
) -> None:
    """``aclose`` stops the bridge, deletes the tempdir, and is idempotent.

    Args:
        fake_sdk: The recording SDK.
    """
    provider = _api_provider()

    async def _go() -> str | None:
        await provider.complete("review")
        workdir = provider._sdk_bridge.workdir if provider._sdk_bridge else None
        await provider.aclose()
        await provider.aclose()
        return workdir

    workdir = asyncio.run(_go())

    assert_that(fake_sdk.clients[0].closed).is_true()
    assert_that(workdir).is_not_none()
    assert_that(Path(workdir or "").exists()).is_false()
    assert_that(provider._sdk_bridge.client if provider._sdk_bridge else 1).is_none()


def test_a_liveness_probe_stops_the_bridge_it_started(fake_sdk: _FakeSdk) -> None:
    """``lintro doctor --ai-liveness`` leaves no bridge process behind.

    Args:
        fake_sdk: The recording SDK.
    """
    provider = _api_provider()

    result = asyncio.run(provider.check_liveness(timeout=1.0))

    assert_that(result.state).is_equal_to(LivenessState.OK)
    assert_that(fake_sdk.clients).is_length(1)
    assert_that(fake_sdk.clients[0].closed).is_true()
    assert_that(provider._sdk_bridge.workdir if provider._sdk_bridge else "x").is_none()
