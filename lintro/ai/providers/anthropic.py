"""Anthropic AI provider implementation.

Uses the Anthropic Python SDK for ``transport: api`` and the ``claude`` CLI
for ``transport: cli``.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
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

_has_anthropic = False
try:
    import anthropic

    _has_anthropic = True
except ImportError:
    pass

DEFAULT_MODEL = PROVIDERS.anthropic.default_model
DEFAULT_API_KEY_ENV = PROVIDERS.anthropic.default_api_key_env
_CLAUDE_BIN = "claude"


def _find_claude() -> str | None:
    """Return the full path to the ``claude`` binary, or None."""
    return CliTransport.find_binary(_CLAUDE_BIN)


class _AnthropicCliTransport(CliTransport):
    """Anthropic ``claude -p`` subprocess transport."""

    def __init__(
        self,
        *,
        binary_path: str,
        model: str,
    ) -> None:
        super().__init__(
            binary_path=binary_path,
            binary_name="Claude",
            install_hint="Install Claude Code: https://code.claude.com/docs/en/setup",
            api_key_env=DEFAULT_API_KEY_ENV,
        )
        self._model = model
        self._supports_schema_name: bool | None = None
        self._capability_lock = threading.Lock()

    def supports_json_schema_name(self) -> bool:
        """Return whether the installed ``claude`` CLI accepts ``--json-schema-name``.

        The current ``@anthropic-ai/claude-code`` (2.1.218) removed the
        ``--json-schema-name`` option, so passing it fails the whole call with
        ``unknown option '--json-schema-name'``. Probe ``claude --help`` once and
        cache whether the flag is advertised, so it is only sent to binaries that
        accept it (#1611). ``--json-schema`` itself is unaffected — only the name
        refinement is gated. A failed probe returns ``False`` (send neither):
        the flag is optional, so omitting it keeps structured output working.

        Returns:
            True when the installed binary advertises ``--json-schema-name``.
        """
        with self._capability_lock:
            if self._supports_schema_name is not None:
                return self._supports_schema_name
            supported = False
            try:
                result = self.run([self._binary_path, "--help"], timeout=10.0)
                help_text = f"{result.stdout or ''}{result.stderr or ''}"
                # Only trust a clean exit: a non-zero --help may echo the flag
                # in an error message without actually supporting it.
                supported = result.returncode == 0 and "--json-schema-name" in help_text
            except (AIProviderError, AINotAvailableError, OSError) as exc:
                # OSError covers PermissionError and other subprocess spawn
                # failures that CliTransport.run() does not remap.
                logger.debug(f"Claude CLI capability probe failed: {exc}")
            self._supports_schema_name = supported
            return supported

    def parse_stdout(self, stdout: str) -> tuple[AIResponse, str | None]:
        """Parse JSON envelope from ``claude --output-format json``."""
        try:
            data = json.loads(stdout.strip())
        except json.JSONDecodeError as exc:
            raise AIProviderError(
                f"Claude CLI returned invalid JSON: {exc}\n"
                f"Raw output: {stdout[:500]}",
            ) from exc

        if data.get("is_error") or data.get("subtype") == "error":
            raise AIProviderError(
                f"Claude CLI reported error: {data.get('result', stdout[:500])}",
            )

        content = data.get("result", "")
        structured = data.get("structured_output")
        if structured is not None:
            content = json.dumps(structured)
        elif isinstance(content, dict):
            content = json.dumps(content)
        elif not isinstance(content, str):
            content = str(content)

        usage = data.get("usage", {})
        input_tokens = int(
            usage.get("input_tokens", usage.get("inputTokens", 0)),
        )
        output_tokens = int(
            usage.get("output_tokens", usage.get("outputTokens", 0)),
        )
        cost = data.get("total_cost_usd")
        if cost is None:
            cost = estimate_cost(self._model, input_tokens, output_tokens)
        else:
            cost = float(cost)

        session_id = data.get("session_id")
        if isinstance(session_id, str) and session_id.strip():
            session_id = session_id.strip()
        else:
            session_id = None

        return (
            AIResponse(
                content=self.substitute_parsed_json(content),
                model=self._model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_estimate=cost,
                provider=AIProvider.ANTHROPIC,
            ),
            session_id,
        )


class AnthropicProvider(BaseAIProvider):
    """Anthropic Claude provider."""

    @staticmethod
    @contextmanager
    def _map_errors() -> Iterator[None]:
        """Map Anthropic SDK exceptions to AI exceptions.

        Safe to call only when the ``anthropic`` SDK is installed —
        the base class ``__init__`` raises ``AINotAvailableError``
        before any method can be called if the SDK is missing.
        """
        try:
            yield
        except anthropic.AuthenticationError as e:
            raise AIAuthenticationError(
                f"Anthropic authentication failed: {e}",
            ) from e
        except anthropic.RateLimitError as e:
            raise AIRateLimitError(
                f"Anthropic rate limit exceeded: {e}",
            ) from e
        except anthropic.AnthropicError as e:
            logger.debug(f"Anthropic API error: {e}")
            raise AIProviderError(
                f"Anthropic API error: {e}",
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
        """Initialize the Anthropic provider.

        Args:
            model: Model identifier. Defaults to claude-sonnet-4-6.
            api_key_env: Environment variable for API key.
                Defaults to ANTHROPIC_API_KEY.
            max_tokens: Default max tokens for completions.
            base_url: Custom API base URL for Anthropic-compatible
                endpoints (proxies, self-hosted, etc.).
            transport: ``api`` for SDK or ``cli`` for ``claude -p``.

        Raises:
            AINotAvailableError: When CLI transport is selected but the
                ``claude`` binary is not on PATH.
        """
        self._transport = transport
        self._cli: _AnthropicCliTransport | None = None

        if transport == AITransport.CLI:
            claude_path = _find_claude()
            if not claude_path:
                raise AINotAvailableError(
                    "Anthropic CLI transport requires the 'claude' binary. "
                    "Install Claude Code: https://code.claude.com/docs/en/setup",
                )
            super().__init__(
                provider_name=AIProvider.ANTHROPIC,
                has_sdk=True,
                sdk_package="claude CLI",
                default_model=DEFAULT_MODEL,
                default_api_key_env=DEFAULT_API_KEY_ENV,
                model=model,
                api_key_env=api_key_env,
                max_tokens=max_tokens,
                base_url=base_url,
                transport=transport,
            )
            self._cli = _AnthropicCliTransport(
                binary_path=claude_path,
                model=self._model,
            )
            self._session_id: str | None = None
            self._session_lock = threading.Lock()
            return

        super().__init__(
            provider_name=AIProvider.ANTHROPIC,
            has_sdk=_has_anthropic,
            sdk_package="anthropic",
            default_model=DEFAULT_MODEL,
            default_api_key_env=DEFAULT_API_KEY_ENV,
            model=model,
            api_key_env=api_key_env,
            max_tokens=max_tokens,
            base_url=base_url,
            transport=transport,
        )

    def _create_client(self, *, api_key: str) -> Any:
        """Create the Anthropic SDK client.

        Args:
            api_key: The resolved API key.

        Returns:
            anthropic.Anthropic: The API client.
        """
        kwargs: dict[str, Any] = {"api_key": api_key}
        if self._base_url:
            kwargs["base_url"] = self._base_url
        return anthropic.Anthropic(**kwargs)

    def is_available(self) -> bool:
        """Return True when the configured transport is usable."""
        if self._transport == AITransport.CLI:
            return _find_claude() is not None
        return super().is_available()

    def _complete_cli(
        self,
        prompt: str,
        *,
        system: str | None,
        timeout: float,
        repo_root: str | None,
        use_one_shot: bool,
        model: str | None = None,
        cli_schema: CliSchemaRequest | None = None,
    ) -> AIResponse:
        if self._cli is None:
            raise AINotAvailableError("Claude CLI transport is not initialized")

        effective_model = model or self._model
        cmd = [
            self._cli._binary_path,
            "--bare",
            "-p",
            prompt,
            "--output-format",
            "json",
            "--permission-mode",
            "dontAsk",
            "--model",
            effective_model,
        ]
        if system:
            cmd.extend(["--append-system-prompt", system])
        if cli_schema is not None:
            cmd.extend(["--json-schema", json.dumps(cli_schema.schema)])
            if cli_schema.schema_name and self._cli.supports_json_schema_name():
                cmd.extend(["--json-schema-name", cli_schema.schema_name])
        with self._session_lock:
            resume_session_id = None if use_one_shot else self._session_id
        if resume_session_id is not None:
            cmd.extend(["--resume", resume_session_id])

        logger.debug(
            f"Claude CLI request: model={effective_model}, "
            f"resume={resume_session_id is not None}, "
            f"prompt_len={len(prompt)}",
        )

        result = self._cli.run(
            cmd,
            timeout=timeout,
            cwd=repo_root or os.getcwd(),
        )
        self._cli.check_exit_code(
            result,
            auth_patterns=("authentication", "login", "not logged in"),
            auth_hint=(
                "Set ANTHROPIC_API_KEY or configure apiKeyHelper "
                "(--bare mode does not use OAuth login)."
            ),
        )

        response, session_id = self._cli.parse_stdout(result.stdout)
        if not use_one_shot and session_id is not None:
            with self._session_lock:
                self._session_id = session_id
        return response

    def complete(
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
        """Generate a completion using Claude (API or CLI).

        Args:
            prompt: The user prompt.
            system: Optional system prompt.
            max_tokens: Maximum tokens to generate (API only).
            timeout: Request timeout in seconds.
            repo_root: Working directory for CLI transport.
            use_one_shot: When True, avoid resuming CLI sessions.
            model: Optional per-call model override.
            cli_schema: Optional native CLI JSON schema request.

        Returns:
            AIResponse: The model's response with usage metadata.
        """
        if self._transport == AITransport.CLI:
            del max_tokens
            return self._complete_cli(
                prompt,
                system=system,
                timeout=timeout,
                repo_root=repo_root,
                use_one_shot=use_one_shot,
                model=model,
                cli_schema=cli_schema,
            )

        del repo_root, use_one_shot, cli_schema
        client = self._get_client()
        effective_model = model or self._model
        # Per-call cap: the lower of the caller's request and the
        # provider-level cap set at init time.
        effective_max = min(max_tokens, self._max_tokens)

        with self._map_errors():
            kwargs: dict[str, Any] = {
                "model": effective_model,
                "max_tokens": effective_max,
                "messages": [{"role": "user", "content": prompt}],
                "timeout": timeout,
            }
            if system:
                kwargs["system"] = system

            response = client.messages.create(**kwargs)

            content = ""
            for block in response.content:
                if hasattr(block, "text"):
                    content += block.text

            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            cost = estimate_cost(effective_model, input_tokens, output_tokens)

            return AIResponse(
                content=content,
                model=effective_model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_estimate=cost,
                provider=AIProvider.ANTHROPIC,
            )

    def stream_complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = DEFAULT_PER_CALL_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT,
        model: str | None = None,
    ) -> AIStreamResult:
        """Stream a completion from the Anthropic API token-by-token.

        Args:
            prompt: The user prompt.
            system: Optional system prompt.
            max_tokens: Maximum tokens to generate.
            timeout: Request timeout in seconds.
            model: Optional per-call model override.

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
        effective_model = model or self._model

        kwargs: dict[str, Any] = {
            "model": effective_model,
            "max_tokens": effective_max,
            "messages": [{"role": "user", "content": prompt}],
            "timeout": timeout,
        }
        if system:
            kwargs["system"] = system

        logger.debug(
            f"Anthropic stream request: model={effective_model}, "
            f"max_tokens={effective_max}",
        )

        final_response: list[AIResponse] = []

        def _generate() -> Iterator[str]:
            with self._map_errors():
                with client.messages.stream(**kwargs) as stream:
                    yield from stream.text_stream
                    final_message = stream.get_final_message()

                input_tokens = final_message.usage.input_tokens
                output_tokens = final_message.usage.output_tokens
                cost = estimate_cost(effective_model, input_tokens, output_tokens)
                final_response.append(
                    AIResponse(
                        content="",
                        model=effective_model,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cost_estimate=cost,
                        provider=AIProvider.ANTHROPIC,
                    ),
                )

        def _on_done() -> AIResponse:
            if not final_response:
                raise AIProviderError(
                    "Anthropic stream was not fully consumed",
                )
            return final_response[0]

        return AIStreamResult(
            _chunks=_generate(),
            _on_done=_on_done,
        )
