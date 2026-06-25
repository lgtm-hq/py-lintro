"""Cursor AI provider implementation.

Invokes the ``agent`` CLI (Cursor's headless agent) for completions. The
``cursor-sdk`` CreateAgent API is not used because it currently returns
internal errors in environments where the ``agent`` binary works reliably.

Authentication is handled by the CLI: ``CURSOR_API_KEY`` or ``agent login``.
"""

from __future__ import annotations

import json
import os
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
from lintro.ai.registry import AIProvider, PROVIDERS

_AGENT_BIN = "agent"
DEFAULT_MODEL = PROVIDERS.cursor.default_model
DEFAULT_API_KEY_ENV = PROVIDERS.cursor.default_api_key_env


def _find_agent() -> str | None:
    """Return the full path to the ``agent`` binary, or None."""
    return shutil.which(_AGENT_BIN)


class CursorProvider(BaseAIProvider):
    """Cursor provider via the ``agent`` CLI."""

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key_env: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        base_url: str | None = None,
    ) -> None:
        agent_path = _find_agent()
        if not agent_path:
            raise AINotAvailableError(
                "Cursor provider requires the 'agent' CLI. "
                "Install with: curl https://cursor.com/install -fsS | bash",
            )

        super().__init__(
            provider_name=AIProvider.CURSOR,
            has_sdk=True,
            sdk_package="agent CLI",
            default_model=DEFAULT_MODEL,
            default_api_key_env=DEFAULT_API_KEY_ENV,
            model=model,
            api_key_env=api_key_env,
            max_tokens=max_tokens,
            base_url=base_url,
        )
        self._agent_path = agent_path
        self._session_id: str | None = None
        self._durable_repo_root: str | None = None

    def _create_client(self, *, api_key: str) -> Any:
        """No persistent client -- each call spawns a subprocess."""
        return None

    def _get_client(self) -> Any:
        """Override: skip API-key validation (the CLI handles auth)."""
        return None

    def is_available(self) -> bool:
        return _find_agent() is not None

    def begin_durable_session(self, *, repo_root: str) -> None:
        """Start a reusable CLI session for single-chunk reviews.

        Args:
            repo_root: Absolute path to the git repository under review.
        """
        self.end_durable_session()
        self._durable_repo_root = repo_root

    def end_durable_session(self) -> None:
        """Clear any active durable CLI session state."""
        self._session_id = None
        self._durable_repo_root = None

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = DEFAULT_PER_CALL_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT,
        repo_root: str | None = None,
        use_one_shot: bool = False,
    ) -> AIResponse:
        """Run a completion via ``agent --print``.

        Args:
            prompt: User prompt text.
            system: Optional system prompt prepended to the user prompt.
            max_tokens: Unused; kept for provider API parity.
            timeout: Subprocess timeout in seconds (minimum 300 for agent).
            repo_root: Git repository root for ``--workspace``.
            use_one_shot: When True, do not resume an existing CLI session.

        Returns:
            Parsed model response with usage metadata.
        """
        del max_tokens
        combined_prompt = prompt
        if system:
            combined_prompt = f"{system}\n\n---\n\n{prompt}"

        effective_root = repo_root or self._durable_repo_root or os.getcwd()
        resume_session = (
            not use_one_shot
            and self._session_id is not None
            and self._durable_repo_root is not None
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
            "--workspace",
            effective_root,
        ]
        if resume_session and self._session_id is not None:
            cmd.extend(["--resume", self._session_id])

        logger.debug(
            f"Cursor CLI request: model={self._model}, "
            f"workspace={effective_root}, resume={resume_session}, "
            f"prompt_len={len(combined_prompt)}",
        )

        effective_timeout = max(timeout, 300.0)
        env = os.environ.copy()
        api_key = os.environ.get(self._api_key_env)
        if api_key:
            env[self._api_key_env] = api_key

        try:
            result = subprocess.run(
                cmd,
                input=combined_prompt,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise AIProviderError(
                f"Cursor CLI timed out after {effective_timeout:.0f}s",
            ) from exc
        except FileNotFoundError as exc:
            raise AINotAvailableError(
                "Cursor 'agent' CLI not found on PATH. "
                "Install with: curl https://cursor.com/install -fsS | bash",
            ) from exc

        if result.returncode != 0:
            stderr = result.stderr.strip()
            if "Authentication required" in stderr or "login" in stderr.lower():
                raise AIAuthenticationError(
                    "Cursor CLI authentication required. "
                    "Run 'agent login' or set CURSOR_API_KEY.",
                )
            raise AIProviderError(
                f"Cursor CLI exited with code {result.returncode}: {stderr}",
            )

        response, session_id = self._parse_json_output(result.stdout)
        if (
            not use_one_shot
            and self._durable_repo_root is not None
            and session_id
        ):
            self._session_id = session_id
        return response

    @staticmethod
    def _extract_json_object(text: str) -> str:
        """Extract the outermost JSON object ``{...}`` from text."""
        start = text.find("{")
        if start == -1:
            return text

        depth = 0
        in_string = False
        escape_next = False
        for index, char in enumerate(text[start:], start=start):
            if escape_next:
                escape_next = False
                continue
            if char == "\\":
                if in_string:
                    escape_next = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start : index + 1]
        return text

    def _parse_json_output(self, stdout: str) -> tuple[AIResponse, str | None]:
        """Parse the JSON envelope from ``agent --output-format json``."""
        try:
            data = json.loads(stdout.strip())
        except json.JSONDecodeError as exc:
            raise AIProviderError(
                f"Cursor CLI returned invalid JSON: {exc}\n"
                f"Raw output: {stdout[:500]}",
            ) from exc

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

        content = self._extract_json_object(content)
        usage = data.get("usage", {})
        session_id = data.get("session_id")
        if isinstance(session_id, str) and session_id.strip():
            session_id = session_id.strip()
        else:
            session_id = None

        return (
            AIResponse(
                content=content,
                model=self._model,
                input_tokens=usage.get("inputTokens", 0),
                output_tokens=usage.get("outputTokens", 0),
                cost_estimate=0.0,
                provider=AIProvider.CURSOR,
            ),
            session_id,
        )
