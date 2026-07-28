"""Abstract base class for AI providers.

Defines the contract that all AI provider implementations must follow.
Shared initialisation, API-key resolution, and availability logic live
here so that concrete providers only implement SDK-specific pieces.
"""

from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from lintro.ai.enums import AITransport
from lintro.ai.exceptions import AIAuthenticationError, AINotAvailableError
from lintro.ai.liveness import (
    LIVENESS_PROBE_PROMPT,
    LIVENESS_TIMEOUT,
    LivenessResult,
    live_result,
    liveness_from_error,
    missing_credential_result,
)
from lintro.ai.providers.async_stream_result import AsyncAIStreamResult
from lintro.ai.providers.capabilities import ProviderCapabilities
from lintro.ai.providers.constants import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_PER_CALL_MAX_TOKENS,
    DEFAULT_TIMEOUT,
)
from lintro.ai.providers.response import AIResponse  # noqa: F401
from lintro.ai.providers.stream_result import AIStreamResult  # noqa: F401

if TYPE_CHECKING:
    from lintro.ai.json_response import CliSchemaRequest
    from lintro.ai.providers.cli_transport import CliTransport

__all__ = [
    "AIResponse",
    "AIStreamResult",
    "AsyncAIStreamResult",
    "BaseAIProvider",
    "LivenessResult",
    "ProviderCapabilities",
]


class BaseAIProvider(ABC):
    """Abstract base class for AI providers.

    Handles common initialisation (model, API-key env var, max tokens,
    base URL), lazy client creation with API-key validation, and the
    ``is_available`` / property boilerplate.

    Subclasses must implement:
    * ``_create_client()`` -- return an SDK-specific client instance.
    * ``complete()`` -- perform the SDK-specific API call and map errors.
    """

    def __init__(
        self,
        *,
        provider_name: str,
        has_sdk: bool,
        sdk_package: str,
        default_model: str,
        default_api_key_env: str,
        model: str | None = None,
        api_key_env: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        base_url: str | None = None,
        transport: AITransport | None = None,
    ) -> None:
        """Initialise the provider with shared parameters.

        Args:
            provider_name: Human-readable provider name (e.g. "anthropic").
            has_sdk: Whether the provider SDK was successfully imported.
            sdk_package: Package name shown in the install hint.
            default_model: Fallback model when *model* is ``None``.
            default_api_key_env: Fallback env-var name when *api_key_env*
                is ``None``.
            model: Model identifier override.
            api_key_env: Environment variable for the API key override.
                The key is required at runtime; its absence raises
                ``AIAuthenticationError`` on first API call.
            max_tokens: Provider-level cap on generated tokens.
            base_url: Custom API base URL.
            transport: Optional transport label for availability checks.

        Raises:
            AINotAvailableError: If the SDK is not installed.
        """
        if not has_sdk:
            raise AINotAvailableError(
                f"{provider_name.title()} provider requires the "
                f"'{sdk_package}' package. "
                "Install with: uv pip install 'lintro[ai]'",
            )

        self._provider_name = provider_name
        self._has_sdk = has_sdk
        self._model = model or default_model
        self._api_key_env = api_key_env or default_api_key_env
        self._max_tokens = max_tokens
        self._base_url = base_url
        self._transport = transport
        self._client: Any = None
        self._client_loop: asyncio.AbstractEventLoop | None = None

    # -- Client management -------------------------------------------------

    @staticmethod
    def _current_loop() -> asyncio.AbstractEventLoop | None:
        """Return the running event loop, or ``None`` outside of one.

        Returns:
            The running loop when called from async code, else ``None``.
        """
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return None

    def _get_client(self) -> Any:
        """Get or lazily create the SDK client for the running event loop.

        Async SDK clients own an HTTP connection pool bound to the event
        loop that created them, so a client built on a *different* loop is
        discarded and rebuilt rather than reused. Callers that stay on a
        single loop (the normal path) always get the cached instance, and a
        client injected outside any loop is left alone.

        Returns:
            The SDK client instance.

        Raises:
            AIAuthenticationError: If no API key is found.
        """
        loop = self._current_loop()
        stale = self._client_loop is not None and self._client_loop is not loop
        if self._client is not None and not stale:
            return self._client

        api_key = os.environ.get(self._api_key_env) or ""
        if not api_key and not self._base_url:
            raise AIAuthenticationError(
                f"No API key found. Set the {self._api_key_env} "
                f"environment variable.",
            )

        if not api_key and self._base_url:
            api_key = "not-needed"

        self._client = self._create_client(api_key=api_key)
        self._client_loop = loop
        return self._client

    @abstractmethod
    def _create_client(self, *, api_key: str) -> Any:
        """Create the SDK-specific client.

        Args:
            api_key: The resolved API key.

        Returns:
            An SDK client instance.
        """
        ...

    # -- Abstract: SDK-specific completion ---------------------------------

    @abstractmethod
    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = DEFAULT_PER_CALL_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT,
        repo_root: str | None = None,
        use_one_shot: bool = False,
        model: str | None = None,
        cli_schema: CliSchemaRequest | None = None,
    ) -> AIResponse:
        """Generate a completion from the AI model.

        Args:
            prompt: The user prompt to send to the model.
            system: Optional system prompt to set context.
            max_tokens: Maximum number of tokens to generate.
            timeout: Request timeout in seconds.
            repo_root: Optional repository root (Cursor provider only).
            use_one_shot: When True, avoid durable sessions (Cursor only).
            model: Optional per-call model override without mutating
                ``model_name``.
            cli_schema: Optional native CLI JSON schema request.

        Returns:
            AIResponse: The model's response with usage metadata.

        Raises:
            AIProviderError: If the API call fails.
            AIAuthenticationError: If authentication fails.
            AIRateLimitError: If rate limited.
        """
        ...

    # -- Streaming (default delegates to complete) --------------------------

    async def stream_complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = DEFAULT_PER_CALL_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT,
        model: str | None = None,
    ) -> AsyncAIStreamResult:
        """Stream a completion. Default: delegates to complete().

        Providers with native streaming support should override this.

        Args:
            prompt: The user prompt text.
            system: Optional system prompt.
            max_tokens: Maximum tokens to generate.
            timeout: Request timeout in seconds.
            model: Optional per-call model override.

        Returns:
            An AsyncAIStreamResult wrapping the token stream.
        """
        response = await self.complete(
            prompt,
            system=system,
            max_tokens=max_tokens,
            timeout=timeout,
            model=model,
        )

        async def _single_chunk() -> AsyncIterator[str]:
            """Yield the whole response as one chunk.

            Yields:
                str: The complete response text.
            """
            yield response.content

        return AsyncAIStreamResult(
            _chunks=_single_chunk(),
            _on_done=lambda: response,
        )

    # -- Capability declaration --------------------------------------------

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Declare what this provider supports on its configured transport.

        The base declaration is deliberately conservative -- nothing is
        supported unless a provider says so -- so a new provider degrades to
        the safe path until it declares otherwise.

        Returns:
            The provider's capability declaration.
        """
        return ProviderCapabilities()

    # -- Credential liveness -----------------------------------------------

    def _cli_transport(self) -> CliTransport | None:
        """Return the CLI transport backing this provider, when it has one.

        Declared here so credential liveness can branch on transport in one
        place rather than being re-implemented per provider. CLI-backed providers
        override it; API-only providers inherit ``None``.

        Returns:
            The provider's CLI transport, or ``None``.
        """
        return None

    async def check_liveness(
        self,
        *,
        timeout: float = LIVENESS_TIMEOUT,
    ) -> LivenessResult:
        """Probe whether this provider's credential can actually serve a call.

        Second step of the ``is_available() -> check_liveness() -> invoke``
        chain. Presence is checked first, then a **minimal real call** is made:
        one token, content-free prompt, response discarded. The real call is the
        point — a depleted account authenticates and lists models perfectly well,
        so nothing short of asking it to generate something distinguishes
        "authed" from "authed and able to serve" (#1826).

        CLI transports take the presence-only path instead (see
        :meth:`~lintro.ai.providers.cli_transport.CliTransport.probe_liveness`):
        a real invocation of a subscription agent CLI is slow and may consume a
        metered turn, so their result reports ``quota_verified=False``.

        Args:
            timeout: Seconds allowed for the probe.

        Returns:
            The classified liveness result; failures are returned, never raised,
            so the caller can surface a visible skip instead of a traceback.
        """
        transport = self._transport
        if transport == AITransport.CLI:
            cli = self._cli_transport()
            if cli is None:
                return missing_credential_result(
                    provider=self._provider_name,
                    transport=transport,
                    message="CLI transport is not initialized",
                    hint="Install the provider CLI and ensure it is on PATH",
                )
            return await cli.probe_liveness(provider_name=self._provider_name)

        if not self.is_available():
            return missing_credential_result(
                provider=self._provider_name,
                transport=transport,
                message=f"{self._api_key_env} is not set",
                hint=f"Export {self._api_key_env} to enable {self._provider_name}",
            )

        try:
            await self.complete(
                LIVENESS_PROBE_PROMPT,
                max_tokens=1,
                timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001 - every failure is a verdict
            return liveness_from_error(
                provider=self._provider_name,
                transport=transport,
                error=exc,
                quota_verified=True,
            )

        return live_result(
            provider=self._provider_name,
            transport=transport,
            quota_verified=True,
            message="credential authenticated and served a probe call",
        )

    def begin_durable_session(self, *, repo_root: str) -> None:
        """Optional hook for providers that reuse CLI sessions.

        Args:
            repo_root: Absolute path to the repository under review.
        """
        del repo_root

    def end_durable_session(self) -> None:
        """Optional hook to tear down a durable provider session."""
        return None

    # -- Concrete shared helpers -------------------------------------------

    def is_available(self) -> bool:
        """Check if this provider is ready to use.

        A provider is available when its SDK is installed and at least
        one of these is true: an API key env var is set, a custom
        base URL is configured, or a client has already been created.

        Returns:
            bool: True if the provider can serve requests.
        """
        if not self._has_sdk:
            return False
        return bool(
            os.environ.get(self._api_key_env)
            or self._base_url
            or self._client is not None,
        )

    @property
    def name(self) -> str:
        """Return the provider's name.

        Returns:
            str: Provider identifier (e.g., "anthropic", "openai").
        """
        return self._provider_name

    @property
    def model_name(self) -> str:
        """Return the configured model name.

        Returns:
            str: Model identifier being used.
        """
        return self._model

    @model_name.setter
    def model_name(self, value: str) -> None:
        """Set the model name.

        Args:
            value: New model identifier.
        """
        self._model = value
