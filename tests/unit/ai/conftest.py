"""Shared fixtures and doubles for AI tests."""

from __future__ import annotations

import asyncio
import subprocess  # nosec B404 - only CompletedProcess objects are constructed here
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.models import AIFixSuggestion
from lintro.ai.providers.base import AIResponse, BaseAIProvider
from lintro.ai.providers.cli_transport import CliTransport
from lintro.ai.registry import AIProvider
from lintro.parsers.base_issue import BaseIssue
from lintro.utils.console.logger import ThreadSafeConsoleLogger


class RecordingConsoleLogger(ThreadSafeConsoleLogger):
    """Console logger that records its output instead of printing it.

    Subclasses the real :class:`ThreadSafeConsoleLogger` so it satisfies the
    type production code annotates, then overrides the two sinks that reach a
    terminal. Every other logger method keeps its real implementation, so text
    that production routes through ``console_output`` is recorded exactly as a
    user would have seen it. Tests use this instead of a mock so they assert
    on visible output rather than on how a collaborator was called (#2315).

    Attributes:
        lines: Every message passed to :meth:`console_output`, in order.
        warnings: Every message passed to :meth:`warning`, in order.
    """

    lines: list[str]
    warnings: list[str]

    def __init__(self) -> None:
        """Start with empty console and warning transcripts."""
        super().__init__()
        self.lines = []
        self.warnings = []

    def console_output(self, text: str, color: str | None = None) -> None:
        """Record one console line instead of printing it.

        Args:
            text: Text the production code wants on the console.
            color: Colour the production code asked for, ignored here.
        """
        self.lines.append(text)

    def error(self, message: str, *_args: Any, **_kwargs: Any) -> None:
        """Record one error instead of printing it.

        The real implementation writes to the terminal directly rather than
        routing through :meth:`console_output`, so it needs its own override
        or the text would escape the transcript (#2315).

        Args:
            message: Error text the production code emitted.
            *_args: Ignored positional extras.
            **_kwargs: Ignored keyword extras.
        """
        self.lines.append(f"ERROR: {message}")

    def warning(self, message: str, **_kwargs: Any) -> None:
        """Record one warning instead of printing and logging it.

        Args:
            message: Warning text the production code emitted.
            **_kwargs: Ignored loguru formatting extras.
        """
        self.warnings.append(message)

    @property
    def text(self) -> str:
        """Return every recorded line joined by newlines.

        Returns:
            str: The console transcript this logger captured.
        """
        return "\n".join(self.lines)


def completed_process(
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Build a ``CompletedProcess`` stand-in for CLI transport tests.

    Args:
        returncode: Process exit code.
        stdout: Process stdout.
        stderr: Process stderr.
        args: Argv the process was invoked with.

    Returns:
        A populated CompletedProcess.
    """
    return subprocess.CompletedProcess(
        args=args or [],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class _FakeProcess:
    """Stand-in for ``asyncio.subprocess.Process``.

    Replays a canned :class:`subprocess.CompletedProcess`, or hangs
    forever when the test is exercising the timeout path.
    """

    def __init__(
        self,
        result: subprocess.CompletedProcess[str] | None,
        *,
        hang: bool = False,
    ) -> None:
        """Store the canned outcome.

        Args:
            result: Result to replay; ``None`` behaves like a clean exit.
            hang: When True, ``communicate`` never returns so the caller's
                timeout fires.
        """
        self._result = result
        self._hang = hang
        self.returncode: int | None = None
        self.killed = False

    async def communicate(
        self,
        payload: bytes | None = None,
    ) -> tuple[bytes, bytes]:
        """Return the canned stdout/stderr pair.

        Args:
            payload: Ignored stdin payload.

        Returns:
            Encoded ``(stdout, stderr)``.
        """
        del payload
        if self._hang:
            await asyncio.Event().wait()
        stdout = (self._result.stdout or "") if self._result else ""
        stderr = (self._result.stderr or "") if self._result else ""
        self.returncode = self._result.returncode if self._result else 0
        return stdout.encode(), stderr.encode()

    def kill(self) -> None:
        """Record that the transport killed the timed-out process."""
        self.killed = True

    async def wait(self) -> int:
        """Reap the killed process.

        Returns:
            The exit code attributed to the kill.
        """
        self.returncode = -9
        return -9


#: Sentinel result telling the fake process to never return output, so the
#: transport's own timeout fires.
HANG = object()


@contextmanager
def patch_cli_exec(**mock_kwargs: Any) -> Iterator[MagicMock]:
    """Patch ``asyncio.create_subprocess_exec`` for CLI transport tests.

    The yielded ``MagicMock`` behaves exactly like a patched
    ``subprocess.run``: configure it with ``return_value`` or
    ``side_effect``, and read recorded argv back as
    ``mock.call_args.args[0]``. Only the spawn mechanism differs -- the
    transport is async, so a fake process replays the configured
    ``CompletedProcess``.

    Args:
        **mock_kwargs: Forwarded to the recording ``MagicMock`` (e.g.
            ``return_value=...`` or ``side_effect=...``). Returning
            :data:`HANG` makes that spawn hang so a timeout can be tested.

    Yields:
        MagicMock: The recording mock standing in for each spawn. Its extra
        ``transport_calls`` attribute records the ``CliTransport.run``
        arguments (``cmd``, ``input_text``, ``timeout``, ``cwd``) that the
        argv alone cannot show, since stdin and timeouts are applied after
        the spawn. Its ``processes`` attribute records every spawned
        :class:`_FakeProcess` so tests can assert on child lifecycle (for
        example that a cancelled call actually killed the child).
    """
    recorder = MagicMock(**mock_kwargs)
    transport_calls: list[SimpleNamespace] = []
    recorder.transport_calls = transport_calls
    processes: list[_FakeProcess] = []
    recorder.processes = processes
    original_run = CliTransport.run

    async def _recording_run(
        transport: CliTransport,
        cmd: list[str],
        *,
        input_text: str | None = None,
        timeout: float,
        cwd: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Record the transport-level call, then run the real implementation.

        Args:
            transport: The transport instance under test.
            cmd: Full argv including the binary path.
            input_text: Optional stdin payload.
            timeout: Subprocess timeout in seconds.
            cwd: Optional working directory.

        Returns:
            Whatever the real ``CliTransport.run`` returns.
        """
        transport_calls.append(
            SimpleNamespace(
                cmd=list(cmd),
                input_text=input_text,
                timeout=timeout,
                cwd=cwd,
            ),
        )
        return await original_run(
            transport,
            cmd,
            input_text=input_text,
            timeout=timeout,
            cwd=cwd,
        )

    async def _fake_exec(*argv: str, **spawn_kwargs: Any) -> _FakeProcess:
        """Spawn a fake process, recording the argv.

        Args:
            *argv: The command argv.
            **spawn_kwargs: Ignored spawn keyword arguments.

        Returns:
            A fake process replaying the configured outcome.

        Raises:
            TypeError: When the configured result is not a CompletedProcess.
        """
        del spawn_kwargs
        result = recorder(list(argv))
        if result is HANG:
            hung_process = _FakeProcess(None, hang=True)
            processes.append(hung_process)
            return hung_process
        if not isinstance(result, subprocess.CompletedProcess):
            raise TypeError(
                "patch_cli_exec needs a CompletedProcess result; "
                f"got {type(result).__name__}",
            )
        process = _FakeProcess(result)
        processes.append(process)
        return process

    with (
        patch("asyncio.create_subprocess_exec", side_effect=_fake_exec),
        patch.object(CliTransport, "run", _recording_run),
    ):
        yield recorder


class MockAIProvider(BaseAIProvider):
    """Thread-safe mock AI provider for testing."""

    def __init__(
        self,
        responses: list[AIResponse] | None = None,
        *,
        available: bool = True,
    ) -> None:
        """Initialize the mock AI provider.

        Args:
            responses: List of responses to return from complete() calls.
            available: Whether the provider reports as available.
        """
        super().__init__(
            provider_name="mock",
            has_sdk=True,
            sdk_package="mock",
            default_model="mock-model",
            default_api_key_env="MOCK_API_KEY",
        )
        self.responses: list[AIResponse] = responses or []
        self.calls: list[dict[str, Any]] = []
        self._available = available
        self._call_index = 0
        self._lock = threading.Lock()

    def _create_client(self, *, api_key: str) -> Any:
        """Return a mock client."""
        return None

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
        cli_schema: object | None = None,
    ) -> AIResponse:
        """Return the next queued response or a default.

        Args:
            prompt: The user prompt.
            system: Optional system prompt.
            max_tokens: Maximum tokens to generate.
            timeout: Request timeout in seconds.
            repo_root: Optional repository root.
            use_one_shot: When True, avoid durable sessions.
            model: Optional per-call model override.
            cli_schema: Optional native CLI JSON schema request.

        Returns:
            The next queued response, or a generic default.
        """
        with self._lock:
            self.calls.append(
                {
                    "prompt": prompt,
                    "system": system,
                    "max_tokens": max_tokens,
                    "timeout": timeout,
                    "repo_root": repo_root,
                    "use_one_shot": use_one_shot,
                    "model": model,
                },
            )
            if self._call_index < len(self.responses):
                response = self.responses[self._call_index]
                self._call_index += 1
                return response
        return AIResponse(
            content="{}",
            model="mock-model",
            input_tokens=10,
            output_tokens=5,
            cost_estimate=0.001,
            provider="mock",
        )

    def is_available(self) -> bool:
        """Check if the mock AI provider is available."""
        return self._available


@dataclass
class MockIssue(BaseIssue):
    """Mock issue with code and severity for testing."""

    code: str = ""
    severity: str = ""
    fixable: bool = False


@pytest.fixture
def mock_provider() -> MockAIProvider:
    """Create a mock AI provider."""
    return MockAIProvider()


@pytest.fixture
def ai_config() -> AIConfig:
    """Create a default AI config for testing."""
    return AIConfig(
        enabled=True,
        provider=AIProvider.ANTHROPIC,
        transport=AITransport.API,
    )


@pytest.fixture
def ai_config_disabled() -> AIConfig:
    """Create a disabled AI config for testing."""
    return AIConfig(enabled=False)


@pytest.fixture
def sample_issues() -> list[MockIssue]:
    """Create sample issues for testing."""
    return [
        MockIssue(
            file="src/main.py",
            line=10,
            column=1,
            message="Use of assert detected",
            code="B101",
            severity="low",
        ),
        MockIssue(
            file="src/utils.py",
            line=25,
            column=5,
            message="Use of assert detected",
            code="B101",
            severity="low",
        ),
        MockIssue(
            file="src/main.py",
            line=42,
            column=1,
            message="Line too long",
            code="E501",
            severity="warning",
        ),
    ]


@pytest.fixture
def sample_fix_suggestions() -> list[AIFixSuggestion]:
    """Create sample fix suggestions for testing."""
    return [
        AIFixSuggestion(
            file="src/main.py",
            line=10,
            code="B101",
            tool_name="bandit",
            original_code="assert x > 0",
            suggested_code="if not x > 0:\n    raise ValueError",
            diff="--- a/src/main.py\n+++ b/src/main.py\n"
            "-assert x > 0\n"
            "+if not x > 0:\n"
            "+    raise ValueError",
            explanation="Replace assert with if/raise",
            confidence="high",
            input_tokens=150,
            output_tokens=80,
            cost_estimate=0.002,
        ),
    ]
