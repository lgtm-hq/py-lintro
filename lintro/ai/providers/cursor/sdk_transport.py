"""Cursor ``transport: api`` through the first-party ``cursor-sdk`` (#3253).

Runs one inference-only prompt per call with
``AsyncAgent.prompt(message, options, client=client)``. The SDK talks to a
bundled native ``cursor-sdk-bridge`` subprocess over loopback; this module owns
that bridge's lifecycle for one provider instance, builds the agent options,
and maps SDK results and errors onto lintro's types.

Inference-only is the whole security contract. lintro's API transport embeds
the diff in the prompt and grants no tools, so the agent gets ``tools=[]``, a
deny list naming every documented tool and tool group, no ``cloud`` block, and
a working directory that is an **empty provider-owned tempdir**, never the
repository under review: a local agent loads ``.cursor/hooks.json``, rules,
``AGENTS.md`` and MCP config from its ``cwd``, and hooks cannot be disabled by
any option. ``~/.cursor/`` on the host still applies.

The SDK is imported only inside the functions that need it, never at module
import or provider construction, so registry parity checks and the unit suite
run without it installed.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import os
import shutil
import signal
import tempfile
import weakref
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from loguru import logger

from lintro.ai.exceptions import (
    AIAuthenticationError,
    AINotAvailableError,
    AIProviderError,
    AIRateLimitError,
)
from lintro.ai.providers.cursor.metadata import CURSOR_METADATA
from lintro.ai.rate_limit import parse_retry_after

__all__ = [
    "CURSOR_API_MIN_TIMEOUT",
    "CURSOR_DISALLOWED_TOOLS",
    "CURSOR_SDK_MODULE",
    "CursorSdkBridge",
    "CursorSdkUsage",
    "api_timeout",
    "map_cursor_sdk_errors",
    "read_run_result",
    "run_prompt",
    "sdk_agent_options",
]

#: Import name of the Cursor SDK.
CURSOR_SDK_MODULE = "cursor_sdk"

#: Floor, in seconds, on one SDK prompt. ``ai.transports.api.timeout`` defaults
#: to 60 s, sized for a direct inference endpoint; a Cursor run also starts an
#: agent behind the bridge, so a floor keeps the default from cutting it off.
CURSOR_API_MIN_TIMEOUT = 180.0

#: Every tool and tool group the SDK documents. ``tools=[]`` already means
#: text only; the deny list is the second lock, because a denial wins over any
#: grant a host-level setting might add.
CURSOR_DISALLOWED_TOOLS: tuple[str, ...] = (
    "shell",
    "edit",
    "mcp",
    "task",
    "webSearch",
    "read",
    "grep",
    "glob",
    "ls",
)

#: The only documented agent modes are ``agent`` and ``plan``; ``plan`` is not
#: documented as read-only, so the tool lists above carry the restriction.
_AGENT_MODE = "agent"

_FINISHED = "finished"


def api_timeout(requested: float) -> float:
    """Return the timeout one SDK prompt is given.

    Args:
        requested: The caller's timeout, from ``ai.transports.api.timeout``.

    Returns:
        *requested*, floored at :data:`CURSOR_API_MIN_TIMEOUT`.
    """
    return max(requested, CURSOR_API_MIN_TIMEOUT)


def _sdk() -> Any:
    """Import and return the Cursor SDK module.

    Returns:
        The ``cursor_sdk`` module.

    Raises:
        AINotAvailableError: When the SDK is not installed.
    """
    try:
        return importlib.import_module(CURSOR_SDK_MODULE)
    except ImportError as exc:
        raise AINotAvailableError(
            "Cursor API transport requires the 'cursor-sdk' package. "
            f"{CURSOR_METADATA.sdk_install_hint}",
        ) from exc


def sdk_agent_options(*, model: str, cwd: str, api_key: str) -> dict[str, Any]:
    """Return the keyword arguments for the SDK's ``AgentOptions``.

    A pure function so the read-only contract can be asserted on the value
    rather than on whatever the SDK did with it.

    Args:
        model: Model identifier to run.
        cwd: The provider-owned empty working directory.
        api_key: The Cursor API key.

    Returns:
        ``AgentOptions`` keyword arguments, with ``local`` holding the
        ``LocalAgentOptions`` keyword arguments. ``cloud`` and
        ``setting_sources`` are deliberately absent.
    """
    return {
        "model": model,
        "api_key": api_key,
        "mode": _AGENT_MODE,
        "tools": [],
        "disallowed_tools": list(CURSOR_DISALLOWED_TOOLS),
        "local": {"cwd": cwd},
    }


def _build_options(sdk: Any, kwargs: dict[str, Any]) -> Any:
    """Turn :func:`sdk_agent_options` output into SDK option objects.

    Args:
        sdk: The ``cursor_sdk`` module.
        kwargs: Keyword arguments from :func:`sdk_agent_options`.

    Returns:
        An ``AgentOptions`` instance.
    """
    local = sdk.LocalAgentOptions(**kwargs["local"])
    return sdk.AgentOptions(**{**kwargs, "local": local})


@dataclass(frozen=True, slots=True)
class CursorSdkUsage:
    """What one SDK run reported back.

    Attributes:
        content: The run's result text, unchanged.
        input_tokens: Reported input tokens (cache tokens included), or 0.
        output_tokens: Reported output tokens, or 0.
        served_model: The model the run names, when it names one.
    """

    content: str
    input_tokens: int
    output_tokens: int
    served_model: str | None


def _token_count(usage: Any, name: str) -> int:
    """Read one token counter, treating anything non-integral as absent.

    Args:
        usage: The SDK ``TokenUsage``, or ``None``.
        name: Attribute to read.

    Returns:
        The count, or 0 when missing.
    """
    value = getattr(usage, name, None)
    return value if isinstance(value, int) and value > 0 else 0


def read_run_result(result: Any) -> CursorSdkUsage:
    """Map an SDK ``RunResult`` onto what lintro records.

    Only ``input_tokens`` and ``output_tokens`` are read: the SDK already
    counts cache reads and writes inside ``input_tokens``, so adding them would
    double-count.

    Args:
        result: The ``RunResult`` returned by ``AsyncAgent.prompt``.

    Returns:
        The result text, token counts and served model.

    Raises:
        AIProviderError: When the run did not finish.
    """
    status = getattr(result, "status", None)
    if status != _FINISHED:
        detail = getattr(result, "error_message", None) or getattr(
            result,
            "error_code",
            None,
        )
        suffix = f": {detail}" if detail else ""
        raise AIProviderError(f"Cursor SDK run {status}{suffix}")
    usage = getattr(result, "usage", None)
    served = getattr(result, "model", None)
    content = getattr(result, "result", None)
    return CursorSdkUsage(
        content=content if isinstance(content, str) else "",
        input_tokens=_token_count(usage, "input_tokens"),
        output_tokens=_token_count(usage, "output_tokens"),
        served_model=served if isinstance(served, str) else None,
    )


def _retry_after(exc: Exception) -> float | None:
    """Return the SDK error's ``retry_after`` in seconds, when parseable.

    Args:
        exc: The SDK rate-limit error.

    Returns:
        Seconds to wait, or ``None``.
    """
    raw = getattr(exc, "retry_after", None)
    if raw is None:
        return None
    return parse_retry_after(str(raw))


@contextlib.contextmanager
def map_cursor_sdk_errors(sdk: Any) -> Iterator[None]:
    """Map Cursor SDK exceptions onto lintro's AI exceptions.

    Args:
        sdk: The ``cursor_sdk`` module whose exception classes to catch.

    Yields:
        None: Control to the wrapped block.

    Raises:
        AIAuthenticationError: On ``AuthenticationError``.
        AIRateLimitError: On ``RateLimitError``, carrying ``retry_after``.
        AINotAvailableError: On ``ConfigurationError``.
        AIProviderError: On any other ``CursorAgentError``.
    """
    try:
        yield
    except sdk.AuthenticationError as exc:
        raise AIAuthenticationError(
            f"Cursor authentication failed: {exc}",
        ) from exc
    except sdk.RateLimitError as exc:
        raise AIRateLimitError(
            f"Cursor rate limit exceeded: {exc}",
            retry_after=_retry_after(exc),
        ) from exc
    except sdk.ConfigurationError as exc:
        raise AINotAvailableError(
            f"Cursor SDK configuration error: {exc}",
        ) from exc
    except sdk.CursorAgentError as exc:
        logger.debug(f"Cursor SDK error: {exc}")
        raise AIProviderError(f"Cursor SDK error: {exc}") from exc


def _kill_bridge(process: Any) -> None:
    """Terminate an orphaned bridge subprocess without an event loop.

    The ``atexit``/garbage-collection fallback for a bridge whose provider was
    never closed: its event loop is usually gone by then, so the async
    ``aclose`` cannot run. Only a process that has not exited is signalled.

    Args:
        process: The bridge's ``asyncio.subprocess.Process``, or ``None``.
    """
    if process is None or getattr(process, "returncode", 0) is not None:
        return
    pid = getattr(process, "pid", None)
    if not isinstance(pid, int):
        return
    with contextlib.suppress(OSError):
        os.kill(pid, signal.SIGTERM)


class CursorSdkBridge:
    """One lazily started bridge, its client and its empty workdir.

    One instance per provider. The bridge starts on the first call, under a
    lock so concurrent calls share it, and is bound to the event loop that
    started it; a call from a different loop retires it and starts another.
    """

    def __init__(self) -> None:
        """Create an idle bridge holder; nothing is started or created."""
        self._client: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None
        self._workdir: str | None = None
        self._finalizer: weakref.finalize[Any, Any] | None = None

    @property
    def workdir(self) -> str | None:
        """Return the empty working directory, once created.

        Returns:
            The tempdir path, or ``None`` before the first call.
        """
        return self._workdir

    @property
    def client(self) -> Any:
        """Return the live SDK client, if a bridge is running.

        Returns:
            The ``AsyncClient``, or ``None``.
        """
        return self._client

    def _lock_for(self, loop: asyncio.AbstractEventLoop) -> asyncio.Lock:
        """Return the start lock for *loop*, creating it on first use.

        Args:
            loop: The running event loop.

        Returns:
            A lock bound to *loop*.
        """
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    def _ensure_workdir(self) -> str:
        """Create the empty working directory on first use.

        Returns:
            Its path.
        """
        if self._workdir is None:
            self._workdir = tempfile.mkdtemp(prefix="lintro-cursor-sdk-")
        return self._workdir

    async def ensure(self, sdk: Any) -> Any:
        """Return a client for the running loop, starting the bridge if needed.

        Args:
            sdk: The ``cursor_sdk`` module.

        Returns:
            The ``AsyncClient`` wired to this instance's bridge.
        """
        loop = asyncio.get_running_loop()
        async with self._lock_for(loop):
            if self._client is not None and self._loop is loop:
                return self._client
            if self._client is not None:
                # Started on a loop that is no longer running this call; its
                # HTTP pool cannot be reused here, so end the process directly.
                self._retire_foreign()
            workdir = self._ensure_workdir()
            client = await sdk.AsyncClient.launch_bridge(workspace=workdir)
            self._client = client
            self._loop = loop
            process = getattr(getattr(client, "_owned_bridge", None), "process", None)
            self._finalizer = weakref.finalize(self, _kill_bridge, process)
            return client

    def _retire_foreign(self) -> None:
        """Kill a bridge started on another loop and forget its client."""
        if self._finalizer is not None:
            self._finalizer()
            self._finalizer = None
        self._client = None
        self._loop = None

    async def aclose(self) -> None:
        """Close the bridge and remove the workdir. Idempotent."""
        client, loop = self._client, self._loop
        self._client = None
        self._loop = None
        try:
            if client is not None:
                if loop is None or loop is asyncio.get_running_loop():
                    with contextlib.suppress(Exception):
                        await client.aclose()
                if self._finalizer is not None:
                    # Run the fallback now: after a clean close the process
                    # has exited and it is a no-op; a foreign-loop client, or
                    # one whose close raised, is terminated by it.
                    self._finalizer()
        finally:
            self._finalizer = None
            workdir, self._workdir = self._workdir, None
            if workdir is not None:
                shutil.rmtree(workdir, ignore_errors=True)


async def run_prompt(
    *,
    bridge: CursorSdkBridge,
    message: str,
    model: str,
    api_key: str,
    timeout: float,
) -> CursorSdkUsage:
    """Run one inference-only prompt and read its result.

    Args:
        bridge: The provider's bridge holder.
        message: The full prompt text.
        model: Model identifier to run.
        api_key: The Cursor API key.
        timeout: Requested timeout; floored at :data:`CURSOR_API_MIN_TIMEOUT`.

    Returns:
        The run's text, token usage and served model.

    Raises:
        AIProviderError: When the run times out or does not finish.
    """
    sdk = _sdk()
    effective_timeout = api_timeout(timeout)
    with map_cursor_sdk_errors(sdk):
        client = await bridge.ensure(sdk)
        workdir = bridge.workdir or ""
        options = _build_options(
            sdk,
            sdk_agent_options(model=model, cwd=workdir, api_key=api_key),
        )
        try:
            result = await asyncio.wait_for(
                sdk.AsyncAgent.prompt(message, options, client=client),
                timeout=effective_timeout,
            )
        except TimeoutError as exc:
            raise AIProviderError(
                f"Cursor SDK run timed out after {effective_timeout:.0f}s",
            ) from exc
    return read_run_result(result)
