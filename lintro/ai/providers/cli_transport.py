"""Shared CLI subprocess transport for AI providers."""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404 - subprocess is the core mechanism for invoking external tools; all invocations use shell=False
from abc import ABC, abstractmethod
from typing import Any

from loguru import logger

from lintro.ai.exceptions import (
    AIAuthenticationError,
    AINotAvailableError,
    AIProviderError,
)
from lintro.ai.transcript import TranscriptDirection, log_transcript_event

__all__ = ["CliTransport"]


class CliTransport(ABC):
    """Base subprocess runner for CLI-backed AI providers."""

    def __init__(
        self,
        *,
        binary_path: str,
        binary_name: str,
        install_hint: str,
        api_key_env: str | None = None,
        provider_name: str | None = None,
    ) -> None:
        """Initialize CLI transport metadata.

        Args:
            binary_path: Absolute path to the CLI executable.
            binary_name: Human-readable binary name for error messages.
            install_hint: Installation guidance shown when the binary is missing.
            api_key_env: Optional environment variable forwarded to subprocesses.
            provider_name: Provider id used for transcript logging.
        """
        self._binary_path = binary_path
        self._binary_name = binary_name
        self._install_hint = install_hint
        self._api_key_env = api_key_env
        self._provider_name = provider_name or binary_name.lower()

    @staticmethod
    def find_binary(name: str) -> str | None:
        """Return the full path to *name* on ``PATH``, if present.

        Args:
            name: Executable name to locate.

        Returns:
            Absolute path, or ``None`` when not found.
        """
        return shutil.which(name)

    def run(
        self,
        cmd: list[str],
        *,
        input_text: str | None = None,
        timeout: float,
        cwd: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Execute a CLI command with timeout and env forwarding.

        Args:
            cmd: Full argv including the binary path.
            input_text: Optional stdin payload.
            timeout: Subprocess timeout in seconds.
            cwd: Optional working directory.

        Returns:
            Completed subprocess result.

        Raises:
            AIProviderError: On timeout.
            AINotAvailableError: When the binary disappears from ``PATH``.
        """
        env = os.environ.copy()

        logger.debug(
            f"{self._binary_name} CLI: cmd={' '.join(cmd[:4])}..., "
            f"timeout={timeout:.0f}s, cwd={cwd}",
        )

        log_transcript_event(
            provider=self._provider_name,
            transport="cli",
            direction=TranscriptDirection.REQUEST,
            payload={
                "cmd": list(cmd),
                "cwd": cwd,
                "timeout": timeout,
                "stdin": input_text,
            },
        )

        try:
            result = subprocess.run(  # nosec B603 - argv is an internally-built list run with shell=False; binary resolved from a known command, no user shell input
                cmd,
                input=input_text,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=env,
                cwd=cwd,
            )
        except subprocess.TimeoutExpired as exc:
            log_transcript_event(
                provider=self._provider_name,
                transport="cli",
                direction=TranscriptDirection.RESPONSE,
                payload={
                    "error": "timeout",
                    "timeout": timeout,
                    "stdout": getattr(exc, "stdout", None),
                    "stderr": getattr(exc, "stderr", None),
                },
            )
            raise AIProviderError(
                f"{self._binary_name} CLI timed out after {timeout:.0f}s",
            ) from exc
        except FileNotFoundError as exc:
            log_transcript_event(
                provider=self._provider_name,
                transport="cli",
                direction=TranscriptDirection.RESPONSE,
                payload={"error": "not_found", "message": str(exc)},
            )
            raise AINotAvailableError(
                f"{self._binary_name} CLI not found on PATH. {self._install_hint}",
            ) from exc

        log_transcript_event(
            provider=self._provider_name,
            transport="cli",
            direction=TranscriptDirection.RESPONSE,
            payload={
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
        )
        return result

    def check_exit_code(
        self,
        result: subprocess.CompletedProcess[str],
        *,
        auth_patterns: tuple[str, ...] = ("authentication", "login"),
        auth_hint: str = "",
    ) -> None:
        """Raise mapped AI exceptions when a CLI exits non-zero.

        Args:
            result: Completed subprocess result.
            auth_patterns: Substrings in stderr that indicate auth failure.
            auth_hint: Guidance appended to authentication errors.

        Raises:
            AIAuthenticationError: When stderr matches auth patterns.
            AIProviderError: For other non-zero exits.
        """
        if result.returncode == 0:
            return

        stderr = result.stderr.strip()
        lowered = stderr.lower()
        for pattern in auth_patterns:
            if pattern in lowered:
                message = f"{self._binary_name} CLI authentication required."
                if auth_hint:
                    message = f"{message} {auth_hint}"
                raise AIAuthenticationError(message)

        raise AIProviderError(
            f"{self._binary_name} CLI exited with code {result.returncode}: {stderr}",
        )

    @staticmethod
    def extract_json_object(text: str) -> str:
        """Extract the outermost JSON object ``{...}`` from text.

        Args:
            text: Raw stdout that may contain prose before JSON.

        Returns:
            Extracted JSON substring, or the original text when none found.
        """
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

    @classmethod
    def substitute_parsed_json(cls, content: str) -> str:
        """Return extracted JSON only when it parses; else keep original text."""
        try:
            json.loads(content)
        except json.JSONDecodeError:
            extracted = cls.extract_json_object(content)
            if extracted != content:
                try:
                    json.loads(extracted)
                except json.JSONDecodeError:
                    pass
                else:
                    return extracted
        return content

    @abstractmethod
    def parse_stdout(self, stdout: str) -> Any:
        """Parse provider-specific stdout into a transport payload.

        Args:
            stdout: Raw stdout from the CLI.

        Returns:
            Provider-specific parsed payload.
        """
        ...
