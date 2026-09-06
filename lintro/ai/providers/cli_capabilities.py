"""Capability guard for CLI-backed AI providers.

This module implements the hybrid capability guard (#1612) that keeps
``--transport cli`` working across agent-CLI releases:

1. **Version floor** -- ``<bin> --version`` is parsed once and compared against
   the provider's declared floor; below it, an actionable error is raised.
2. **Proactive ``--help`` gate** -- optional flags are only sent when the
   installed binary advertises them. ``--help`` and ``--version`` do not call
   the model, so the gate costs no quota.
3. **Reactive backstop** -- when a call still fails with ``unknown option``
   naming an optional flag, that flag is dropped and the call retried, so a
   help surface lintro could not read never turns into a hard failure.

The guard is a collaborator rather than a mixin: it owns the probe caches and
the lock that protects them, and it runs its probes through a callable supplied
by its owner. :class:`~lintro.ai.providers.cli_transport.CliTransport` passes
its own ``run`` and re-exposes the guard's methods, so providers and liveness
checks call the same names they always did (#1871).

The flag surface itself is declared in :mod:`lintro.ai.providers.cli_contracts`.
"""

from __future__ import annotations

import re
import subprocess  # nosec B404 - the guard only inspects results produced by its owner's runner
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from loguru import logger

from lintro.ai.enums import AITransport
from lintro.ai.exceptions import (
    AINotAvailableError,
    AIProviderError,
)
from lintro.ai.liveness import (
    LivenessResult,
    incompatible_cli_result,
    live_result,
)
from lintro.ai.providers.cli_contracts import (
    CliContract,
    flag_named_in,
    format_version,
    unadvertised_flags,
)

__all__ = [
    "PROBE_TIMEOUT",
    "UNKNOWN_OPTION_PATTERNS",
    "CliCapabilityGuard",
    "OptionalArg",
]

#: Signature of the callable the guard runs its probes through: the owning
#: transport's ``run``, which spawns the subprocess and records the transcript.
ProbeRunner = Callable[..., Awaitable[subprocess.CompletedProcess[str]]]

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


class CliCapabilityGuard:
    """Probe cache and contract checks for one CLI binary.

    Owned by a :class:`~lintro.ai.providers.cli_transport.CliTransport`, which
    supplies the runner the free ``--help`` / ``--version`` probes go through.
    All probe results are memoised here, so a transport probes each binary at
    most once per process (racing callers may duplicate a read-only probe; see
    :meth:`binary_version`).
    """

    def __init__(
        self,
        *,
        run: ProbeRunner,
        binary_path: str,
        binary_name: str,
        install_hint: str,
        contract: CliContract | None = None,
    ) -> None:
        """Initialize the capability guard.

        Args:
            run: Awaitable runner used for the free capability probes. The
                owning transport passes its own ``run`` so probes are spawned
                and transcribed exactly like a real call.
            binary_path: Absolute path to the CLI executable.
            binary_name: Human-readable binary name for error messages.
            install_hint: Installation guidance shown when the binary is not
                runnable.
            contract: Declared flag surface and version floor for this binary.
                When omitted, the guard is inert and every optional flag is
                sent as-is.
        """
        self._run = run
        self._binary_path = binary_path
        self._binary_name = binary_name
        self._install_hint = install_hint
        self._contract = contract
        self._capability_lock = threading.RLock()
        self._version: tuple[int, ...] | None = None
        self._version_probed = False
        self._help_text: str | None = None
        self._flag_support: dict[str, bool] = {}

    def note_unsupported_flag(self, flag: str) -> None:
        """Remember that the binary rejected *flag* at runtime.

        The reactive backstop calls this after dropping a flag so the proactive
        gate stops offering it for the rest of the process.

        Args:
            flag: The rejected flag, e.g. ``--resume``.
        """
        with self._capability_lock:
            self._flag_support[flag] = False

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

    async def binary_version(self) -> tuple[int, ...] | None:
        """Return the installed binary's version, probing at most once.

        The subprocess probe runs outside the memo lock -- holding a
        thread lock across an ``await`` would stall the event loop. Racing
        callers may therefore each spawn one probe; the probe is read-only
        and idempotent, so the only cost is a duplicated ``--version``.

        Returns:
            Parsed version components, or ``None`` when the probe fails or the
            output carries no recognisable version.
        """
        with self._capability_lock:
            if self._version_probed:
                return self._version

        args = self._contract.version_args if self._contract else ("--version",)
        output = await self._probe([self._binary_path, *args])
        version = self.parse_version(output) if output is not None else None

        with self._capability_lock:
            if not self._version_probed:
                self._version_probed = True
                self._version = version
            resolved = self._version
        logger.debug(
            f"{self._binary_name} CLI version probe: {format_version(resolved)}",
        )
        return resolved

    async def check_version_floor(self) -> None:
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

        version = await self.binary_version()
        if version is None or version >= contract.version_floor:
            return

        raise AINotAvailableError(
            f"{self._binary_name} CLI {format_version(version)} is older than the "
            f"minimum supported {format_version(contract.version_floor)}. "
            f"{contract.upgrade_hint}",
        )

    async def missing_required_flags(self) -> tuple[str, ...]:
        """Return declared required flags the installed binary no longer offers.

        Required flags are never gated at runtime -- dropping one would hang or
        badly degrade a call -- so this is the check that turns their
        disappearance into a signal instead of a mystery failure. It is also what
        the tier-1 contract test asserts on, so flag drift breaks CI rather than a
        user's review.

        Returns:
            The missing flags in contract order. Empty when the contract declares
            none, when there is no contract, or when the help text could not be
            read at all -- an unreadable help surface is not evidence of absence.
        """
        contract = self._contract
        if contract is None or not contract.required_flags:
            return ()
        help_text = await self.help_text()
        if help_text is None:
            return ()
        return unadvertised_flags(
            lowered_help=help_text.lower(),
            flags=contract.required_flags,
        )

    async def probe_liveness(self, *, provider_name: str) -> LivenessResult:
        """Probe whether this CLI can serve a call, without making one.

        Deliberately presence-only. A real invocation of a subscription agent CLI
        is slow and may consume a metered turn, so the probe is limited to the
        free capability surface the hybrid guard already reads: the binary is on
        ``PATH``, it answers at least one probe, it meets the declared version
        floor, and it still advertises every required flag. The result carries
        ``quota_verified=False`` -- a depleted subscription is invisible here and
        only surfaces at invocation time, where the shared error taxonomy turns it
        into a visible failure rather than a silent one.

        Args:
            provider_name: Provider identifier used in the result.

        Returns:
            The liveness result for this CLI transport.
        """
        try:
            await self.check_version_floor()
        except AINotAvailableError as exc:
            return incompatible_cli_result(provider=provider_name, message=str(exc))

        missing = await self.missing_required_flags()
        if missing:
            contract = self._contract
            hint = contract.upgrade_hint if contract is not None else None
            return incompatible_cli_result(
                provider=provider_name,
                message=(
                    f"{self._binary_name} CLI no longer advertises required "
                    f"flag(s): {', '.join(missing)}"
                ),
                hint=hint,
            )

        version = await self.binary_version()
        if version is None and await self.help_text() is None:
            # Neither free probe produced usable output. Being on ``PATH`` is not
            # the same as being runnable — a broken install (missing native
            # binary, wrong architecture, bad permissions) looks exactly like
            # this, and reporting it live would be the silent pass this probe
            # exists to prevent.
            return incompatible_cli_result(
                provider=provider_name,
                message=(
                    f"{self._binary_name} CLI at {self._binary_path} answered "
                    "neither --version nor --help; the install is not runnable"
                ),
                hint=self._install_hint,
            )

        return live_result(
            provider=provider_name,
            transport=AITransport.CLI,
            quota_verified=False,
            message=(
                f"{self._binary_name} CLI {format_version(version)} is installed "
                "and matches lintro's declared flag surface (quota not verified — "
                "presence-only probe)"
            ),
        )

    async def help_text(self) -> str | None:
        """Return the binary's help output, probing at most once.

        As with :meth:`binary_version`, the probe runs outside the memo
        lock so the event loop is never blocked; a racing caller may
        duplicate the (free, read-only) ``--help`` invocation.

        Returns:
            Help text, or ``None`` when the probe failed or exited non-zero.
            A non-zero ``--help`` is not trusted: an error message can echo a
            flag the binary does not actually support.
        """
        with self._capability_lock:
            if self._help_text is not None:
                return self._help_text or None

        args = self._contract.help_args if self._contract else ("--help",)
        output = await self._probe([self._binary_path, *args])

        with self._capability_lock:
            if self._help_text is None:
                self._help_text = output or ""
            return self._help_text or None

    async def supports_flag(self, flag: str) -> bool:
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

        help_text = await self.help_text()
        # Token-boundary match, not substring: a bare ``flag in help_text`` would
        # read ``--json-schema`` as advertised whenever the help lists only
        # ``--json-schema-name``.
        supported = (
            True if help_text is None else flag_named_in(help_text.lower(), flag)
        )
        with self._capability_lock:
            self._flag_support.setdefault(flag, supported)
            return self._flag_support[flag]

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
        kept: list[OptionalArg] = []
        for arg in optional_args:
            if await self.supports_flag(arg.flag):
                kept.append(arg)
                continue
            logger.debug(
                f"{self._binary_name} CLI does not advertise {arg.flag}; omitting it",
            )
        return kept

    async def apply_optional_args(
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
        accepted = await self.filter_optional_args(candidates)
        for arg in accepted:
            cmd.extend(arg.as_argv())
        return accepted

    def flag_purpose(self, flag: str) -> str:
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
    def rejected_optional_arg(
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
            if flag_named_in(lowered, candidate.flag):
                return candidate
        return None

    @staticmethod
    def strip_optional_arg(
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

    async def _probe(self, cmd: list[str]) -> str | None:
        """Run a free capability probe and return its combined output.

        Args:
            cmd: Full argv for the probe (``--help`` or ``--version``).

        Returns:
            Combined stdout/stderr on a clean exit, or ``None`` when the probe
            failed or exited non-zero.
        """
        try:
            result = await self._run(cmd, timeout=PROBE_TIMEOUT)
        except (AIProviderError, AINotAvailableError, OSError) as exc:
            # OSError covers PermissionError and other spawn failures that
            # run() does not remap.
            logger.debug(f"{self._binary_name} CLI probe failed: {exc}")
            return None
        if result.returncode != 0:
            return None
        return f"{result.stdout or ''}{result.stderr or ''}"
