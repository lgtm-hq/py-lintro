"""Shared CLI subprocess transport for AI providers.

This module owns the subprocess mechanics: spawning an agent CLI without
blocking the event loop, timeouts and cancellation, exit-code mapping, and the
transcript hooks. The capability guard that decides *what* may be sent to a
given binary lives next door in
:mod:`lintro.ai.providers.cli_capabilities`; :class:`CliTransport` owns one
:class:`~lintro.ai.providers.cli_capabilities.CliCapabilityGuard` and re-exposes
its methods, so providers and liveness checks keep calling them on the
transport (#1871).

The flag surface itself is declared in :mod:`lintro.ai.providers.cli_contracts`.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import json
import os
import shutil
import signal
import subprocess  # nosec B404 - subprocess is the core mechanism for invoking external tools; all invocations use shell=False
from abc import ABC, abstractmethod
from typing import Any

from loguru import logger

from lintro.ai.exceptions import (
    AIAuthenticationError,
    AINotAvailableError,
    AIProviderError,
)
from lintro.ai.liveness import LivenessResult
from lintro.ai.providers.cli_capabilities import (
    PROBE_TIMEOUT,
    UNKNOWN_OPTION_PATTERNS,
    CliCapabilityGuard,
    OptionalArg,
)
from lintro.ai.providers.cli_contracts import CliContract, flag_named_in
from lintro.ai.transcript import cli_transcript

__all__ = [
    "PROBE_TIMEOUT",
    "UNKNOWN_OPTION_PATTERNS",
    "CliTransport",
    "OptionalArg",
    "flag_named_in",
]


async def _terminate(process: asyncio.subprocess.Process) -> None:
    """Kill a child process (and its session) so it cannot linger as a zombie.

    The child is started with ``start_new_session=True`` so an agent
    ``killpg`` cannot take lintro with it. Termination therefore has to
    signal that new process group, not only the direct child pid.

    Args:
        process: The child process to stop.
    """
    pid = getattr(process, "pid", None)
    if pid is not None and os.name == "posix":
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(pid, signal.SIGKILL)
    with contextlib.suppress(ProcessLookupError):
        process.kill()
    with contextlib.suppress(ProcessLookupError):
        await process.wait()


def _decode(raw: bytes | None) -> str:
    """Decode subprocess output, tolerating invalid byte sequences.

    Args:
        raw: Raw bytes captured from the child process, or ``None``.

    Returns:
        Decoded text; never ``None`` so callers can treat it like
        ``subprocess.run(text=True)`` output.
    """
    if not raw:
        return ""
    return raw.decode("utf-8", errors="replace")


class CliTransport(ABC):
    """Base subprocess runner for CLI-backed AI providers."""

    def __init__(
        self,
        *,
        binary_path: str,
        binary_name: str,
        install_hint: str,
        api_key_env: str | None = None,
        contract: CliContract | None = None,
        provider_name: str | None = None,
    ) -> None:
        """Initialize CLI transport metadata.

        Args:
            binary_path: Absolute path to the CLI executable.
            binary_name: Human-readable binary name for error messages.
            install_hint: Installation guidance shown when the binary is missing.
            api_key_env: Optional environment variable forwarded to subprocesses.
            contract: Declared flag surface and version floor for this binary.
                When omitted, the capability guard is inert and every optional
                flag is sent as-is.
            provider_name: Provider id recorded in transcript events. Defaults
                to a lowercased ``binary_name``.
        """
        self._binary_path = binary_path
        self._binary_name = binary_name
        self._install_hint = install_hint
        self._api_key_env = api_key_env
        self._provider_name = provider_name or binary_name.lower()
        self._guard = CliCapabilityGuard(
            run=self.run,
            binary_path=binary_path,
            binary_name=binary_name,
            install_hint=install_hint,
            contract=contract,
        )

    # -- Capability guard ---------------------------------------------------
    #
    # The guard itself lives in :mod:`lintro.ai.providers.cli_capabilities`.
    # These are delegating shims: providers, liveness and the contract tests
    # call the capability surface on the transport, so the method names stay
    # here even though the state and the logic moved (#1871).

    @property
    def capabilities(self) -> CliCapabilityGuard:
        """Return the capability guard backing this transport.

        Returns:
            The guard that owns the probe caches and contract checks.
        """
        return self._guard

    @property
    def contract(self) -> CliContract | None:
        """Return the declared CLI contract, when one was supplied.

        Returns:
            The contract, or ``None`` for unguarded transports.
        """
        return self._guard.contract

    @staticmethod
    def parse_version(text: str) -> tuple[int, ...] | None:
        """Extract a version tuple from ``--version`` output.

        Args:
            text: Raw ``--version`` output.

        Returns:
            Version components, or ``None`` when no version is recognisable.
        """
        return CliCapabilityGuard.parse_version(text)

    async def binary_version(self) -> tuple[int, ...] | None:
        """Return the installed binary's version, probing at most once.

        Returns:
            Parsed version components, or ``None`` when the probe fails or the
            output carries no recognisable version.
        """
        return await self._guard.binary_version()

    async def check_version_floor(self) -> None:
        """Verify the installed binary meets the declared version floor.

        Propagates ``AINotAvailableError`` from the guard when the installed
        version is below the contract's floor.
        """
        await self._guard.check_version_floor()

    async def missing_required_flags(self) -> tuple[str, ...]:
        """Return declared required flags the installed binary no longer offers.

        Returns:
            The missing flags in contract order; empty when nothing is missing
            or the help surface could not be read.
        """
        return await self._guard.missing_required_flags()

    async def probe_liveness(self, *, provider_name: str) -> LivenessResult:
        """Probe whether this CLI can serve a call, without making one.

        Args:
            provider_name: Provider identifier used in the result.

        Returns:
            The liveness result for this CLI transport.
        """
        return await self._guard.probe_liveness(provider_name=provider_name)

    async def help_text(self) -> str | None:
        """Return the binary's help output, probing at most once.

        Returns:
            Help text, or ``None`` when the probe failed or exited non-zero.
        """
        return await self._guard.help_text()

    async def supports_flag(self, flag: str) -> bool:
        """Return whether the installed binary advertises *flag*.

        Args:
            flag: Flag to look for, e.g. ``--json-schema-name``.

        Returns:
            True when the flag may be sent.
        """
        return await self._guard.supports_flag(flag)

    async def filter_optional_args(
        self,
        optional_args: list[OptionalArg],
    ) -> list[OptionalArg]:
        """Drop optional args the installed binary does not advertise.

        Args:
            optional_args: Candidate optional flags in call order.

        Returns:
            The subset that passed the proactive ``--help`` gate.
        """
        return await self._guard.filter_optional_args(optional_args)

    async def apply_optional_args(
        self,
        cmd: list[str],
        candidates: list[OptionalArg],
    ) -> list[OptionalArg]:
        """Gate *candidates* through ``--help`` and append the survivors to *cmd*.

        Args:
            cmd: The argv being built; accepted flags are appended to it.
            candidates: Optional flags to gate, in call order.

        Returns:
            The optional args that passed the proactive gate.
        """
        return await self._guard.apply_optional_args(cmd, candidates)

    async def run_guarded(
        self,
        cmd: list[str],
        *,
        optional_args: list[OptionalArg] | None = None,
        input_text: str | None = None,
        timeout: float,
        cwd: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run *cmd*, dropping optional flags the binary rejects and retrying.

        *cmd* must already contain every entry of *optional_args*; the caller
        decides where each flag sits in the argv. Version-floor enforcement runs
        once before the first invocation.

        Args:
            cmd: Full argv including the binary path and any optional flags.
            optional_args: Optional flags present in *cmd* that may be dropped.
            input_text: Optional stdin payload.
            timeout: Subprocess timeout in seconds.
            cwd: Optional working directory.

        Returns:
            The completed subprocess result. Exit-code mapping stays with the
            caller via :meth:`check_exit_code`. Propagates
            ``AINotAvailableError`` from :meth:`check_version_floor` when the
            binary is below its declared floor.
        """
        await self.check_version_floor()

        argv = list(cmd)
        remaining = list(optional_args or [])
        while True:
            result = await self.run(
                argv,
                input_text=input_text,
                timeout=timeout,
                cwd=cwd,
            )
            if result.returncode == 0:
                return result

            offender = self._guard.rejected_optional_arg(result.stderr, remaining)
            if offender is None:
                return result

            logger.warning(
                f"{self._binary_name} CLI rejected {offender.flag}; retrying "
                f"without it (this call loses: "
                f"{self._guard.flag_purpose(offender.flag)}).",
            )
            self._guard.note_unsupported_flag(offender.flag)
            argv = self._guard.strip_optional_arg(argv, offender)
            remaining = [item for item in remaining if item.flag != offender.flag]

    @staticmethod
    def find_binary(name: str) -> str | None:
        """Return the full path to *name* on ``PATH``, if present.

        Args:
            name: Executable name to locate.

        Returns:
            Absolute path, or ``None`` when not found.
        """
        return shutil.which(name)

    async def run(
        self,
        cmd: list[str],
        *,
        input_text: str | None = None,
        timeout: float,
        cwd: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Execute a CLI command with timeout and env forwarding.

        Spawns the child with ``asyncio.create_subprocess_exec`` so provider
        calls never block the event loop. The return type stays
        ``subprocess.CompletedProcess`` so stdout parsers and
        :meth:`check_exit_code` are unchanged.

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
            asyncio.CancelledError: Re-raised after the child is stopped.
        """
        env = os.environ.copy()

        logger.debug(
            f"{self._binary_name} CLI: cmd={' '.join(cmd[:4])}..., "
            f"timeout={timeout:.0f}s, cwd={cwd}",
        )

        with cli_transcript(
            provider=self._provider_name,
            cmd=cmd,
            cwd=cwd,
            timeout=timeout,
            stdin=input_text,
        ) as record:
            try:
                spawn_kwargs: dict[str, Any] = {}
                if os.name == "posix":
                    # New session: the agent CLI cannot killpg lintro (#2156).
                    spawn_kwargs["start_new_session"] = True
                process = await asyncio.create_subprocess_exec(  # nosec B603 - argv is an internally-built list; exec form takes no shell
                    *cmd,
                    stdin=asyncio.subprocess.PIPE if input_text is not None else None,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                    cwd=cwd,
                    **spawn_kwargs,
                )
            except FileNotFoundError as exc:
                raise AINotAvailableError(
                    f"{self._binary_name} CLI not found on PATH. {self._install_hint}",
                ) from exc
            except OSError as exc:
                # Defense in depth for #1967: even after stdin prompt delivery,
                # a leftover oversized argv element (or a huge env) can still
                # raise E2BIG / "Argument list too long". Map it to a provider
                # error instead of leaking a raw OSError to callers.
                if (
                    exc.errno == errno.E2BIG
                    or "argument list too long"
                    in str(
                        exc,
                    ).lower()
                ):
                    raise AIProviderError(
                        f"{self._binary_name} CLI could not spawn because the "
                        "argument list is too long (E2BIG). Large prompts must "
                        "be delivered via stdin, not argv. Narrow with --paths "
                        "or use --transport api when available.",
                    ) from exc
                raise AIProviderError(
                    f"{self._binary_name} CLI failed to start: {exc}",
                ) from exc

            payload = input_text.encode() if input_text is not None else None
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(payload),
                    timeout=timeout,
                )
            except TimeoutError as exc:
                await _terminate(process)
                raise AIProviderError(
                    f"{self._binary_name} CLI timed out after {timeout:.0f}s",
                ) from exc
            except asyncio.CancelledError:
                # A cancelled review (e.g. a sibling chunk failed) must not
                # leave an agent CLI running against the repository.
                await _terminate(process)
                raise

            result = subprocess.CompletedProcess(
                args=list(cmd),
                returncode=process.returncode if process.returncode is not None else 0,
                stdout=_decode(stdout_bytes),
                stderr=_decode(stderr_bytes),
            )
            record(
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
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

        The failure cause is taken from stderr, falling back to stdout when
        stderr is empty or whitespace-only. Several agent CLIs report fatal
        errors on stdout inside their JSON envelope while leaving stderr empty
        (a logged-out ``claude`` exits 1 with
        ``{"is_error": true, ..., "result": "Not logged in ..."}``), so a
        stderr-only cause silently drops the diagnostic and defeats the auth
        patterns. stderr stays preferred when present so a chatty but
        successfully parsed stdout cannot pollute a real stderr diagnostic.

        Args:
            result: Completed subprocess result.
            auth_patterns: Substrings in the resolved cause text (stderr, or
                stdout when stderr is empty) that indicate auth failure.
            auth_hint: Guidance appended to authentication errors.

        Raises:
            AIAuthenticationError: When the cause text matches auth patterns.
            AIProviderError: For other non-zero exits.
        """
        if result.returncode == 0:
            return

        cause = result.stderr.strip() or result.stdout.strip()
        lowered = cause.lower()
        for pattern in auth_patterns:
            if pattern in lowered:
                message = f"{self._binary_name} CLI authentication required."
                if auth_hint:
                    message = f"{message} {auth_hint}"
                raise AIAuthenticationError(message)

        raise AIProviderError(
            f"{self._binary_name} CLI exited with code {result.returncode}: {cause}",
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
