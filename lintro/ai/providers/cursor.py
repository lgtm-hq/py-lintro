"""Cursor AI provider implementation.

Shells out to the ``agent`` CLI (Cursor's headless agent) to access
any model available on the user's Cursor subscription. No SDK
dependency -- requires only the ``agent`` binary on ``PATH``.

Authentication is handled by the CLI itself: either ``agent login``
(interactive, stores credentials in the macOS Keychain) or the
``CURSOR_API_KEY`` environment variable.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

from loguru import logger

from lintro.ai.exceptions import (
    AIAuthenticationError,
    AINotAvailableError,
    AIProviderError,
)
from lintro.ai.providers.base import AIResponse, BaseAIProvider
from lintro.ai.providers.constants import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_PER_CALL_MAX_TOKENS,
    DEFAULT_TIMEOUT,
)
from lintro.ai.registry import AIProvider

_AGENT_BIN = "agent"
_DEFAULT_MODEL = "auto"
_DEFAULT_API_KEY_ENV = "CURSOR_API_KEY"


def _find_agent() -> str | None:
    """Return the full path to the ``agent`` binary, or None."""
    return shutil.which(_AGENT_BIN)


class CursorProvider(BaseAIProvider):
    """Cursor provider via the ``agent`` CLI.

    Unlike the Anthropic and OpenAI providers, this does **not** use a
    Python SDK. Instead it invokes ``agent --print --output-format json``
    as a subprocess and parses the structured output.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key_env: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        base_url: str | None = None,
    ) -> None:
        """Initialize the Cursor provider.

        Args:
            model: Model identifier override.
            api_key_env: Environment variable for the API key override.
            max_tokens: Provider-level cap on generated tokens.
            base_url: Custom API base URL (unused for CLI).

        Raises:
            AINotAvailableError: If the ``agent`` CLI is not on PATH.
        """
        agent_path = _find_agent()
        if not agent_path:
            raise AINotAvailableError(
                "Cursor provider requires the 'agent' CLI. "
                "Install with: curl https://cursor.com/install -fsS | bash",
            )

        # BaseAIProvider checks has_sdk; we always pass True since there
        # is no Python package to import -- only the CLI binary.
        super().__init__(
            provider_name=AIProvider.CURSOR,
            has_sdk=True,
            sdk_package="agent CLI",
            default_model=_DEFAULT_MODEL,
            default_api_key_env=_DEFAULT_API_KEY_ENV,
            model=model,
            api_key_env=api_key_env,
            max_tokens=max_tokens,
            base_url=base_url,
        )
        self._agent_path = agent_path

    # -- BaseAIProvider contract -------------------------------------------

    def _create_client(self, *, api_key: str) -> Any:
        """No persistent client -- each call spawns a subprocess."""
        return None

    def _get_client(self) -> Any:
        """Override: skip API-key validation (the CLI handles auth)."""
        return None

    def is_available(self) -> bool:
        """Check if the ``agent`` CLI is on PATH.

        Returns:
            True when the ``agent`` binary is discoverable.
        """
        return _find_agent() is not None

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = DEFAULT_PER_CALL_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> AIResponse:
        """Run a completion via ``agent --print``.

        The system prompt is prepended to the user prompt since the
        CLI does not have a dedicated system-prompt flag.
        """
        effective_max = min(max_tokens, self._max_tokens)
        combined_prompt = prompt
        if system:
            combined_prompt = f"{system}\n\n---\n\n{prompt}"
        combined_prompt = (
            f"{combined_prompt}\n\n[Respond in at most {effective_max} tokens.]"
        )

        cmd = [
            self._agent_path,
            "--print",
            "--output-format",
            "json",
            "--trust",
            "--mode",
            "ask",
            "--model",
            self._model,
        ]

        logger.debug(
            f"Cursor CLI request: model={self._model}, "
            f"max_tokens={effective_max}, "
            f"prompt_len={len(combined_prompt)}",
        )

        # Pipe via stdin to avoid OS argument-length limits on large prompts.
        try:
            result = subprocess.run(
                cmd,
                input=combined_prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise AIProviderError(
                f"Cursor CLI timed out after {timeout}s",
            ) from e
        except FileNotFoundError as e:
            raise AINotAvailableError(
                "Cursor 'agent' CLI not found on PATH. "
                "Install with: curl https://cursor.com/install -fsS | bash",
            ) from e

        if result.returncode != 0:
            stderr = result.stderr.strip()
            if "Authentication required" in stderr or "login" in stderr:
                raise AIAuthenticationError(
                    "Cursor CLI authentication required. "
                    "Run 'agent login' or set CURSOR_API_KEY.",
                )
            raise AIProviderError(
                f"Cursor CLI exited with code {result.returncode}: {stderr}",
            )

        return self._parse_json_output(result.stdout)

    @staticmethod
    def _extract_json_object(text: str) -> str:
        """Extract the outermost JSON object ``{...}`` from text.

        The ``agent`` CLI sometimes prepends prose before JSON output.
        This finds the first ``{`` and matches its closing ``}`` using
        brace counting, returning the substring. Falls back to the
        original text if no balanced object is found.
        """
        start = text.find("{")
        if start == -1:
            return text

        depth = 0
        in_string = False
        escape_next = False
        for i in range(start, len(text)):
            c = text[i]
            if escape_next:
                escape_next = False
                continue
            if c == "\\":
                if in_string:
                    escape_next = True
                continue
            if c == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        return text

    def _parse_json_output(self, stdout: str) -> AIResponse:
        """Parse the JSON envelope from ``agent --output-format json``."""
        try:
            data = json.loads(stdout.strip())
        except json.JSONDecodeError as e:
            raise AIProviderError(
                f"Cursor CLI returned invalid JSON: {e}\n"
                f"Raw output: {stdout[:500]}",
            ) from e

        if data.get("is_error") or data.get("subtype") == "error":
            raise AIProviderError(
                f"Cursor CLI reported error: {data.get('result', stdout[:500])}",
            )

        content = data.get("result", "")
        if not content:
            logger.warning(
                f"Cursor CLI returned empty result. "
                f"Full response: {json.dumps(data)[:1000]}",
            )

        # The agent CLI may prefix JSON with prose. Only extract when the
        # result is not already valid JSON, so plain-text answers with
        # incidental braces are not silently truncated.
        try:
            json.loads(content)
        except json.JSONDecodeError:
            content = self._extract_json_object(content)

        usage = data.get("usage", {})
        # agent --output-format json does not expose token counts; default to 0.
        input_tokens = usage.get("inputTokens", 0)
        output_tokens = usage.get("outputTokens", 0)

        return AIResponse(
            content=content,
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_estimate=0.0,
            provider=AIProvider.CURSOR,
        )
