"""Shared CLI subprocess transport for AI providers.

Besides running the subprocess, this module implements the hybrid capability
guard (#1612) that keeps ``--transport cli`` working across agent-CLI releases:

1. **Version floor** -- ``<bin> --version`` is parsed once and compared against
   the provider's declared floor; below it, an actionable error is raised.
2. **Proactive ``--help`` gate** -- optional flags are only sent when the
   installed binary advertises them. ``--help`` and ``--version`` do not call
   the model, so the gate costs no quota.
3. **Reactive backstop** -- when a call still fails with ``unknown option``
   naming an optional flag, that flag is dropped and the call retried, so a
   help surface lintro could not read never turns into a hard failure.

The flag surface itself is declared in :mod:`lintro.ai.providers.cli_contracts`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess  # nosec B404 - subprocess is the core mechanism for invoking external tools; all invocations use shell=False
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from lintro.ai.exceptions import (
    AIAuthenticationError,
    AINotAvailableError,
    AIProviderError,
)
from lintro.ai.providers.cli_contracts import CliContract, format_version

__all__ = ["CliTransport", "OptionalArg"]

#: stderr fragments emitted by argument parsers when a flag is not recognised.
#: Covers commander.js (claude, cursor agent) and clap (codex).
UNKNOWN_OPTION_PATTERNS: tuple[str, ...] = (
    "unknown option",
    "unknown flag",
    "unknown argument",
    "unrecognized option",
    "unrecognized argument",
    "unexpected argument",
    "no such option",
    "invalid option",
)

#: Seconds allowed for the free ``--help`` / ``--version`` capability probes.
PROBE_TIMEOUT: float = 10.0

# Deliberately strict: a full three-component version delimited by
# non-version characters. A looser pattern happily matches decimals embedded in
# unrelated output (``"total_cost_usd": 0.01``) and would mis-detect a binary as
# ancient.
_VERSION_RE = re.compile(r"(?:^|[\s(v=])(\d+)\.(\d+)\.(\d+)(?![\d.])")

# Flag characters that continue a flag token. Used to bound flag matches in
# stderr so ``--foo`` does not match inside ``--foobar``.
_FLAG_CHAR = r"[0-9a-z-]"


def _flag_named_in(lowered_stderr: str, flag: str) -> bool:
    """Return whether *flag* is named in *lowered_stderr* as a whole token.

    A plain substring test would let a rejection of ``--foobar`` also match a
    candidate ``--foo``, dropping the wrong optional flag. Matching on flag-token
    boundaries keeps the backstop precise even if a future contract adds flags
    that share a prefix.

    Args:
        lowered_stderr: The already-lowercased stderr text.
        flag: The candidate flag, e.g. ``--resume``.

    Returns:
        True when the flag appears as a complete token.
    """
    pattern = rf"(?<!{_FLAG_CHAR}){re.escape(flag.lower())}(?!{_FLAG_CHAR})"
    return re.search(pattern, lowered_stderr) is not None


@dataclass(frozen=True, slots=True)
class OptionalArg:
    """An optional flag (and its values) as it appears in an argv list.

    Attributes:
        flag: The flag itself, e.g. ``--resume``.
        values: Values that follow the flag and must be dropped with it.
    """

    flag: str
    values: tuple[str, ...] = field(default=())

    def as_argv(self) -> list[str]:
        """Return the flag and its values as an argv fragment.

        Returns:
            List of argv tokens.
        """
        return [self.flag, *self.values]


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
        """
        self._binary_path = binary_path
        self._binary_name = binary_name
        self._install_hint = install_hint
        self._api_key_env = api_key_env
        self._contract = contract
        self._capability_lock = threading.RLock()
        self._version: tuple[int, ...] | None = None
        self._version_probed = False
        self._help_text: str | None = None
        self._flag_support: dict[str, bool] = {}

    # -- Capability guard ---------------------------------------------------

    @property
    def contract(self) -> CliContract | None:
        """Return the declared CLI contract, when one was supplied.

        Returns:
            The contract, or ``None`` for unguarded transports.
        """
        return self._contract

    @staticmethod
    def parse_version(text: str) -> tuple[int, ...] | None:
        """Extract a version tuple from ``--version`` output.

        Handles both semver (``2.1.218``) and the calendar versioning the
        Cursor ``agent`` CLI uses (``2026.07.09-a3815c0``). Only the first line
        is considered, and only a full three-component version counts, so noise
        further down a help banner cannot masquerade as a version.

        Args:
            text: Raw ``--version`` output.

        Returns:
            Version components, or ``None`` when no version is recognisable.
        """
        first_line = (text or "").strip().splitlines()
        if not first_line:
            return None
        match = _VERSION_RE.search(first_line[0])
        if match is None:
            return None
        return tuple(int(part) for part in match.groups())

    def binary_version(self) -> tuple[int, ...] | None:
        """Return the installed binary's version, probing at most once.

        Returns:
            Parsed version components, or ``None`` when the probe fails or the
            output carries no recognisable version.
        """
        with self._capability_lock:
            if self._version_probed:
                return self._version
            self._version_probed = True
            args = self._contract.version_args if self._contract else ("--version",)
            output = self._probe([self._binary_path, *args])
            self._version = self.parse_version(output) if output is not None else None
            logger.debug(
                f"{self._binary_name} CLI version probe: "
                f"{format_version(self._version)}",
            )
            return self._version

    def check_version_floor(self) -> None:
        """Verify the installed binary meets the declared version floor.

        The comparison runs on every call rather than latching a
        "already checked" flag: :meth:`binary_version` is memoised, so the check
        is cheap, and evaluating it each time closes the race where a second
        thread could see a latched flag and proceed before the first thread's
        comparison had actually raised.

        An unknown version is never treated as a failure: probing can fail for
        reasons unrelated to compatibility, and the ``--help`` gate plus the
        reactive backstop still protect the call.

        Raises:
            AINotAvailableError: When the installed version is below the floor.
        """
        contract = self._contract
        if contract is None or contract.version_floor is None:
            return

        version = self.binary_version()
        if version is None or version >= contract.version_floor:
            return

        raise AINotAvailableError(
            f"{self._binary_name} CLI {format_version(version)} is older than the "
            f"minimum supported {format_version(contract.version_floor)}. "
            f"{contract.upgrade_hint}",
        )

    def help_text(self) -> str | None:
        """Return the binary's help output, probing at most once.

        Returns:
            Help text, or ``None`` when the probe failed or exited non-zero.
            A non-zero ``--help`` is not trusted: an error message can echo a
            flag the binary does not actually support.
        """
        with self._capability_lock:
            if self._help_text is not None:
                return self._help_text or None
            args = self._contract.help_args if self._contract else ("--help",)
            output = self._probe([self._binary_path, *args])
            self._help_text = output or ""
            return self._help_text or None

    def supports_flag(self, flag: str) -> bool:
        """Return whether the installed binary advertises *flag*.

        A flag the reactive backstop has already seen rejected is remembered as
        unsupported. When the help text cannot be read at all, the answer is
        optimistic (``True``): sending the flag and letting the backstop drop it
        costs one extra subprocess, whereas assuming absence would silently
        degrade every call against a binary that supports it.

        Args:
            flag: Flag to look for, e.g. ``--json-schema-name``.

        Returns:
            True when the flag may be sent.
        """
        with self._capability_lock:
            cached = self._flag_support.get(flag)
        if cached is not None:
            return cached

        help_text = self.help_text()
        # Token-boundary match, not substring: a bare ``flag in help_text`` would
        # read ``--json-schema`` as advertised whenever the help lists only
        # ``--json-schema-name``.
        supported = (
            True if help_text is None else _flag_named_in(help_text.lower(), flag)
        )
        with self._capability_lock:
            self._flag_support.setdefault(flag, supported)
            return self._flag_support[flag]

    def filter_optional_args(
        self,
        optional_args: list[OptionalArg],
    ) -> list[OptionalArg]:
        """Drop optional args the installed binary does not advertise.

        Args:
            optional_args: Candidate optional flags in call order.

        Returns:
            The subset that passed the proactive ``--help`` gate.
        """
        kept: list[OptionalArg] = []
        for arg in optional_args:
            if self.supports_flag(arg.flag):
                kept.append(arg)
                continue
            logger.debug(
                f"{self._binary_name} CLI does not advertise {arg.flag}; omitting it",
            )
        return kept

    def apply_optional_args(
        self,
        cmd: list[str],
        candidates: list[OptionalArg],
    ) -> list[OptionalArg]:
        """Gate *candidates* through ``--help`` and append the survivors to *cmd*.

        Mutates *cmd* in place so callers keep control of where the optional
        block sits relative to trailing positionals (e.g. Codex's prompt). The
        returned list must be passed to :meth:`run_guarded` as ``optional_args``
        so the reactive backstop can drop any that the binary still rejects.

        Args:
            cmd: The argv being built; accepted flags are appended to it.
            candidates: Optional flags to gate, in call order.

        Returns:
            The optional args that passed the proactive gate.
        """
        accepted = self.filter_optional_args(candidates)
        for arg in accepted:
            cmd.extend(arg.as_argv())
        return accepted

    def run_guarded(
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
        self.check_version_floor()

        argv = list(cmd)
        remaining = list(optional_args or [])
        while True:
            result = self.run(
                argv,
                input_text=input_text,
                timeout=timeout,
                cwd=cwd,
            )
            if result.returncode == 0:
                return result

            offender = self._rejected_optional_arg(result.stderr, remaining)
            if offender is None:
                return result

            logger.warning(
                f"{self._binary_name} CLI rejected {offender.flag}; retrying "
                f"without it (this call loses: "
                f"{self._flag_purpose(offender.flag)}).",
            )
            with self._capability_lock:
                self._flag_support[offender.flag] = False
            argv = self._strip_optional_arg(argv, offender)
            remaining = [item for item in remaining if item.flag != offender.flag]

    def _flag_purpose(self, flag: str) -> str:
        """Return the declared purpose of *flag* for degradation messages.

        Args:
            flag: The flag being dropped.

        Returns:
            The declared purpose, or a generic fallback.
        """
        if self._contract is not None:
            for declared in self._contract.optional_flags:
                if declared.flag == flag:
                    return declared.purpose
        return "an optional capability"

    @staticmethod
    def _rejected_optional_arg(
        stderr: str,
        candidates: list[OptionalArg],
    ) -> OptionalArg | None:
        """Find the optional arg an ``unknown option`` error is complaining about.

        Args:
            stderr: Raw stderr from the failed invocation.
            candidates: Optional args still present in the argv.

        Returns:
            The offending arg, or ``None`` when stderr is not a flag-surface
            rejection of a known optional flag.
        """
        lowered = (stderr or "").lower()
        if not any(pattern in lowered for pattern in UNKNOWN_OPTION_PATTERNS):
            return None
        for candidate in candidates:
            if _flag_named_in(lowered, candidate.flag):
                return candidate
        return None

    @staticmethod
    def _strip_optional_arg(
        argv: list[str],
        arg: OptionalArg,
    ) -> list[str]:
        """Return *argv* with *arg* and its values removed.

        Args:
            argv: The argv list to filter.
            arg: The optional arg to remove.

        Returns:
            A new argv list without the flag or its values.
        """
        try:
            index = argv.index(arg.flag)
        except ValueError:
            return list(argv)
        stripped = list(argv)
        del stripped[index : index + 1 + len(arg.values)]
        return stripped

    def _probe(self, cmd: list[str]) -> str | None:
        """Run a free capability probe and return its combined output.

        Args:
            cmd: Full argv for the probe (``--help`` or ``--version``).

        Returns:
            Combined stdout/stderr on a clean exit, or ``None`` when the probe
            failed or exited non-zero.
        """
        try:
            result = self.run(cmd, timeout=PROBE_TIMEOUT)
        except (AIProviderError, AINotAvailableError, OSError) as exc:
            # OSError covers PermissionError and other spawn failures that
            # run() does not remap.
            logger.debug(f"{self._binary_name} CLI probe failed: {exc}")
            return None
        if result.returncode != 0:
            return None
        return f"{result.stdout or ''}{result.stderr or ''}"

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

        try:
            return subprocess.run(  # nosec B603 - argv is an internally-built list run with shell=False; binary resolved from a known command, no user shell input
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
            raise AIProviderError(
                f"{self._binary_name} CLI timed out after {timeout:.0f}s",
            ) from exc
        except FileNotFoundError as exc:
            raise AINotAvailableError(
                f"{self._binary_name} CLI not found on PATH. {self._install_hint}",
            ) from exc

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
