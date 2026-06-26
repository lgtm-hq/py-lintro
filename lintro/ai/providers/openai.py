"""OpenAI AI provider implementation.

Uses the OpenAI Python SDK for ``transport: api`` and ``codex exec`` for
``transport: cli``.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from loguru import logger

from lintro.ai.cost import estimate_cost
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import (
    AIAuthenticationError,
    AINotAvailableError,
    AIProviderError,
    AIRateLimitError,
)
from lintro.ai.json_response import CliSchemaRequest
from lintro.ai.providers.base import AIResponse, AIStreamResult, BaseAIProvider
from lintro.ai.providers.cli_transport import CliTransport
from lintro.ai.providers.constants import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_PER_CALL_MAX_TOKENS,
    DEFAULT_TIMEOUT,
)
from lintro.ai.registry import PROVIDERS, AIProvider

_has_openai = False
try:
    import openai

    _has_openai = True
except ImportError:
    pass

DEFAULT_MODEL = PROVIDERS.openai.default_model
DEFAULT_API_KEY_ENV = PROVIDERS.openai.default_api_key_env
_CODEX_BIN = "codex"
_CODEX_AUTH_PATH = Path.home() / ".codex" / "auth.json"


def _find_codex() -> str | None:
    """Return the full path to the ``codex`` binary, or None."""
    return CliTransport.find_binary(_CODEX_BIN)


def _codex_authenticated() -> bool:
    """Return True when Codex CLI auth is likely configured."""
    if os.environ.get("CODEX_API_KEY"):
        return True
    return _CODEX_AUTH_PATH.is_file()


class _CodexCliTransport(CliTransport):
    """OpenAI Codex ``codex exec`` subprocess transport."""

    def __init__(
        self,
        *,
        binary_path: str,
        model: str,
    ) -> None:
        super().__init__(
            binary_path=binary_path,
            binary_name="Codex",
            install_hint="Install Codex CLI: https://developers.openai.com/codex/cli",
            api_key_env="CODEX_API_KEY",
        )
        self._model = model

    def parse_stdout(self, stdout: str) -> AIResponse:
        """Parse JSONL stdout and extract the final agent message."""
        final_text = ""
        input_tokens = 0
        output_tokens = 0

        for line in stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                continue

            event_type = event.get("type", "")
            if event_type == "item.completed":
                item = event.get("item", {})
                if item.get("type") == "agent_message":
                    final_text = item.get("text", final_text)
            elif event_type == "turn.completed":
                usage = event.get("usage", {})
                input_tokens = int(usage.get("input_tokens", input_tokens))
                output_tokens = int(usage.get("output_tokens", output_tokens))
                structured = event.get("structured_output")
                if structured is not None:
                    final_text = json.dumps(structured)
            elif event_type == "error":
                raise AIProviderError(
                    f"Codex CLI reported error: {event.get('message', stripped)}",
                )

        if not final_text:
            # Fallback: single JSON object envelope
            try:
                data = json.loads(stdout.strip())
                final_text = data.get("result", data.get("output", ""))
                usage = data.get("usage", {})
                input_tokens = int(usage.get("input_tokens", input_tokens))
                output_tokens = int(usage.get("output_tokens", output_tokens))
            except json.JSONDecodeError as exc:
                raise AIProviderError(
                    f"Codex CLI returned unparsable output: {exc}\n"
                    f"Raw output: {stdout[:500]}",
                ) from exc

        cost = estimate_cost(self._model, input_tokens, output_tokens)
        return AIResponse(
            content=self.extract_json_object(final_text),
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_estimate=cost,
            provider=AIProvider.OPENAI,
        )


class OpenAIProvider(BaseAIProvider):
    """OpenAI GPT provider."""

    @staticmethod
    @contextmanager
    def _map_errors() -> Iterator[None]:
        """Map OpenAI SDK exceptions to AI exceptions.

        Safe to call only when the ``openai`` SDK is installed —
        the base class ``__init__`` raises ``AINotAvailableError``
        before any method can be called if the SDK is missing.
        """
        try:
            yield
        except openai.AuthenticationError as e:
            raise AIAuthenticationError(
                f"OpenAI authentication failed: {e}",
            ) from e
        except openai.RateLimitError as e:
            raise AIRateLimitError(
                f"OpenAI rate limit exceeded: {e}",
            ) from e
        except openai.OpenAIError as e:
            logger.debug(f"OpenAI API error: {e}")
            raise AIProviderError(
                f"OpenAI API error: {e}",
            ) from e

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key_env: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        base_url: str | None = None,
        transport: AITransport = AITransport.API,
    ) -> None:
        """Initialize the OpenAI provider.

        Args:
            model: Model identifier. Defaults to gpt-4o.
            api_key_env: Environment variable for API key.
                Defaults to OPENAI_API_KEY.
            max_tokens: Default max tokens for completions.
            base_url: Custom API base URL for OpenAI-compatible
                endpoints (Ollama, vLLM, Azure OpenAI, etc.).
            transport: ``api`` for SDK or ``cli`` for ``codex exec``.

        Raises:
            AINotAvailableError: When CLI transport is selected but the
                ``codex`` binary is not on PATH.
        """
        self._transport = transport
        self._cli: _CodexCliTransport | None = None

        if transport == AITransport.CLI:
            codex_path = _find_codex()
            if not codex_path:
                raise AINotAvailableError(
                    "OpenAI CLI transport requires the 'codex' binary. "
                    "Install Codex CLI: https://developers.openai.com/codex/cli",
                )
            super().__init__(
                provider_name=AIProvider.OPENAI,
                has_sdk=True,
                sdk_package="codex CLI",
                default_model=DEFAULT_MODEL,
                default_api_key_env=DEFAULT_API_KEY_ENV,
                model=model,
                api_key_env=api_key_env,
                max_tokens=max_tokens,
                base_url=base_url,
                transport=transport,
            )
            self._cli = _CodexCliTransport(
                binary_path=codex_path,
                model=self._model,
            )
            return

        super().__init__(
            provider_name=AIProvider.OPENAI,
            has_sdk=_has_openai,
            sdk_package="openai",
            default_model=DEFAULT_MODEL,
            default_api_key_env=DEFAULT_API_KEY_ENV,
            model=model,
            api_key_env=api_key_env,
            max_tokens=max_tokens,
            base_url=base_url,
            transport=transport,
        )

    def _create_client(self, *, api_key: str) -> Any:
        """Create the OpenAI SDK client.

        Args:
            api_key: The resolved API key.

        Returns:
            openai.OpenAI: The API client.
        """
        kwargs: dict[str, Any] = {"api_key": api_key}
        if self._base_url:
            kwargs["base_url"] = self._base_url
        return openai.OpenAI(**kwargs)

    def is_available(self) -> bool:
        """Return True when the configured transport is usable."""
        if self._transport == AITransport.CLI:
            return _find_codex() is not None
        return super().is_available()

    def _complete_cli(
        self,
        prompt: str,
        *,
        timeout: float,
        repo_root: str | None,
        cli_schema: CliSchemaRequest | None = None,
    ) -> AIResponse:
        if self._cli is None:
            raise AINotAvailableError("Codex CLI transport is not initialized")

        cmd = [
            self._cli._binary_path,
            "exec",
            "--json",
            "--sandbox",
            "read-only",
        ]
        if cli_schema is not None:
            cmd.extend(["--output-schema", json.dumps(cli_schema.schema)])
        cmd.append(prompt)

        logger.debug(
            f"Codex CLI request: model={self._model}, prompt_len={len(prompt)}",
        )

        result = self._cli.run(
            cmd,
            timeout=timeout,
            cwd=repo_root or os.getcwd(),
        )
        self._cli.check_exit_code(
            result,
            auth_patterns=("authentication", "login", "not authenticated"),
            auth_hint="Run 'codex login' or set CODEX_API_KEY.",
        )
        return self._cli.parse_stdout(result.stdout)

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = DEFAULT_PER_CALL_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT,
        repo_root: str | None = None,
        use_one_shot: bool = False,
        cli_schema: CliSchemaRequest | None = None,
    ) -> AIResponse:
        """Generate a completion using GPT (API or Codex CLI).

        Args:
            prompt: The user prompt.
            system: Optional system prompt.
            max_tokens: Maximum tokens to generate (API only).
            timeout: Request timeout in seconds.
            repo_root: Working directory for CLI transport (git repo).
            use_one_shot: Unused for Codex; accepted for API parity.
            cli_schema: Optional native CLI JSON schema request.

        Returns:
            AIResponse: The model's response with usage metadata.
        """
        if self._transport == AITransport.CLI:
            del max_tokens, use_one_shot
            combined = prompt
            if system:
                combined = f"{system}\n\n---\n\n{prompt}"
            return self._complete_cli(
                combined,
                timeout=timeout,
                repo_root=repo_root,
                cli_schema=cli_schema,
            )

        del repo_root, use_one_shot, cli_schema
        client = self._get_client()
        effective_max = min(max_tokens, self._max_tokens)

        with self._map_errors():
            messages: list[dict[str, str]] = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            response = client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=effective_max,
                timeout=timeout,
            )

            content = response.choices[0].message.content or ""

            input_tokens = 0
            output_tokens = 0
            if response.usage:
                input_tokens = response.usage.prompt_tokens
                output_tokens = response.usage.completion_tokens

            cost = estimate_cost(self._model, input_tokens, output_tokens)

            return AIResponse(
                content=content,
                model=self._model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_estimate=cost,
                provider=AIProvider.OPENAI,
            )

    def stream_complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = DEFAULT_PER_CALL_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> AIStreamResult:
        """Stream a completion from the OpenAI API token-by-token.

        Args:
            prompt: The user prompt.
            system: Optional system prompt.
            max_tokens: Maximum tokens to generate.
            timeout: Request timeout in seconds.

        Returns:
            An AIStreamResult wrapping the token stream.
        """
        if self._transport == AITransport.CLI:
            return super().stream_complete(
                prompt,
                system=system,
                max_tokens=max_tokens,
                timeout=timeout,
            )

        client = self._get_client()
        effective_max = min(max_tokens, self._max_tokens)

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        logger.debug(
            f"OpenAI stream request: model={self._model}, "
            f"max_tokens={effective_max}",
        )

        final_response: list[AIResponse] = []
        accumulated_text: list[str] = []

        def _generate() -> Iterator[str]:
            with self._map_errors():
                stream = client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    max_tokens=effective_max,
                    timeout=timeout,
                    stream=True,
                    stream_options={"include_usage": True},
                )

                input_tokens = 0
                output_tokens = 0

                for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        text = chunk.choices[0].delta.content
                        accumulated_text.append(text)
                        yield text
                    if chunk.usage:
                        input_tokens = chunk.usage.prompt_tokens
                        output_tokens = chunk.usage.completion_tokens

                cost = estimate_cost(self._model, input_tokens, output_tokens)
                final_response.append(
                    AIResponse(
                        content="".join(accumulated_text),
                        model=self._model,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cost_estimate=cost,
                        provider=AIProvider.OPENAI,
                    ),
                )

        def _on_done() -> AIResponse:
            if not final_response:
                raise AIProviderError(
                    "OpenAI stream was not fully consumed",
                )
            return final_response[0]

        return AIStreamResult(
            _chunks=_generate(),
            _on_done=_on_done,
        )
