"""Verify an installed agent CLI against lintro's declared contract.

The hybrid capability guard (#1612) keeps a *single* review working when an agent
CLI drops a flag: optional flags are gated by ``--help`` and dropped-and-retried
when rejected. What it cannot do is tell anyone that the drift happened, and it
deliberately does not gate required flags — dropping one of those would hang or
badly degrade the call rather than degrade gracefully.

This module closes that gap. It reads the installed binary's free capability
surface (``--version`` and ``--help``, neither of which calls a model, so neither
costs quota) and reports how it diverges from
:mod:`lintro.ai.providers.cli_contracts`. That report is what the tier-1 contract
test asserts on, so a removed required flag breaks CI on the day the CLI ships it
instead of breaking a user's review weeks later (#1611 shipped exactly that way:
``@anthropic-ai/claude-code`` 2.1.218 removed ``--json-schema-name`` and nothing
noticed until a review failed).

Kept separate from :class:`~lintro.ai.providers.cli_transport.CliTransport`
because verification must not need a provider: the check runs against whatever
binary is on ``PATH``, with no SDK, no credential, and no configuration.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
from dataclasses import dataclass

from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers.cli_contracts import (
    CLI_CONTRACTS,
    CliContract,
    cli_contract_for,
    format_version,
)
from lintro.ai.providers.cli_transport import (
    PROBE_TIMEOUT,
    CliTransport,
    flag_named_in,
)

__all__ = [
    "CliSurfaceReport",
    "declared_cli_providers",
    "probe_cli_surface",
    "probe_cli_surface_sync",
]


@dataclass(frozen=True, slots=True)
class CliSurfaceReport:
    """How an installed agent CLI compares to its declared contract.

    Attributes:
        provider: Provider the contract belongs to.
        contract: The declared contract that was checked against.
        binary_path: Resolved path to the binary, or ``None`` when absent.
        version: Parsed version, or ``None`` when unreadable.
        help_readable: Whether ``--help`` exited cleanly with output. A binary
            whose help cannot be read yields no flag findings at all — absence of
            evidence, not evidence of absence.
        missing_required_flags: Declared required flags the help text no longer
            advertises, in contract order.
        unadvertised_optional_flags: Declared optional flags the help text does
            not advertise. Informational: the guard gates these before sending
            them, so their absence degrades a call rather than breaking it.
    """

    provider: AIProvider
    contract: CliContract
    binary_path: str | None
    version: tuple[int, ...] | None
    help_readable: bool
    missing_required_flags: tuple[str, ...] = ()
    unadvertised_optional_flags: tuple[str, ...] = ()

    @property
    def binary_present(self) -> bool:
        """Return whether the declared binary was found on ``PATH``.

        Returns:
            True when the binary resolved to a path.
        """
        return self.binary_path is not None

    @property
    def meets_version_floor(self) -> bool:
        """Return whether the installed version satisfies the declared floor.

        An unreadable version is not a failure, matching the guard's runtime
        behaviour: probing can fail for reasons unrelated to compatibility.

        Returns:
            True when no floor is declared, the version is unknown, or the
            version is at or above the floor.
        """
        floor = self.contract.version_floor
        if floor is None or self.version is None:
            return True
        return self.version >= floor

    @property
    def violations(self) -> tuple[str, ...]:
        """Return the contract breaches that should fail a build.

        Only conditions lintro cannot degrade around count: a below-floor binary
        and a vanished required flag. Unadvertised *optional* flags are excluded
        by design — the guard already handles them.

        Returns:
            Human-readable violation descriptions; empty when the CLI conforms.
        """
        problems: list[str] = []
        if not self.binary_present:
            problems.append(
                f"{self.contract.binary} not found on PATH "
                f"({self.contract.upgrade_hint})",
            )
            return tuple(problems)
        if not self.meets_version_floor:
            problems.append(
                f"{self.contract.binary} {format_version(self.version)} is below "
                f"the declared floor {format_version(self.contract.version_floor)}",
            )
        if self.missing_required_flags:
            problems.append(
                f"{self.contract.binary} no longer advertises required flag(s): "
                f"{', '.join(self.missing_required_flags)}",
            )
        return tuple(problems)

    def describe(self) -> str:
        """Render a one-line summary for CI logs and test failure messages.

        Returns:
            A summary naming the binary, its version, and any violations.
        """
        if not self.binary_present:
            return f"{self.contract.binary}: not installed"
        state = "; ".join(self.violations) if self.violations else "contract satisfied"
        help_note = "" if self.help_readable else " (help unreadable)"
        return (
            f"{self.contract.binary} {format_version(self.version)}"
            f"{help_note}: {state}"
        )


async def _probe(*, binary_path: str, args: tuple[str, ...]) -> str | None:
    """Run a free capability probe and return its combined output.

    Args:
        binary_path: Resolved path to the binary.
        args: Argv suffix (``--version`` or the contract's help args).

    Returns:
        Combined stdout/stderr on a clean exit, or ``None`` when the probe failed
        or exited non-zero. A non-zero probe is never trusted: an error message
        can echo a flag the binary does not actually support.
    """
    try:
        process = await asyncio.create_subprocess_exec(  # nosec B603 - fixed argv from a declared contract, shell=False
            binary_path,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError:
        return None

    try:
        raw_stdout, raw_stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=PROBE_TIMEOUT,
        )
    except TimeoutError:
        # The child may already have exited between the timeout and the kill, so
        # a stalled probe must not turn into a ProcessLookupError traceback.
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        with contextlib.suppress(ProcessLookupError):
            await process.wait()
        return None

    if process.returncode != 0:
        return None
    stdout = (raw_stdout or b"").decode("utf-8", errors="replace")
    stderr = (raw_stderr or b"").decode("utf-8", errors="replace")
    return f"{stdout}{stderr}"


async def probe_cli_surface(*, provider: AIProvider) -> CliSurfaceReport:
    """Probe one provider's agent CLI and report contract divergence.

    Args:
        provider: Provider whose declared CLI contract should be verified.

    Returns:
        The surface report. A binary that is not installed yields a report with
        ``binary_present`` false rather than raising, so a caller decides whether
        absence is a skip (a developer machine) or a failure (the contract gate).
    """
    contract = cli_contract_for(provider)
    binary_path = shutil.which(contract.binary)
    if binary_path is None:
        return CliSurfaceReport(
            provider=provider,
            contract=contract,
            binary_path=None,
            version=None,
            help_readable=False,
        )

    version_output = await _probe(
        binary_path=binary_path,
        args=contract.version_args,
    )
    version = (
        CliTransport.parse_version(version_output)
        if version_output is not None
        else None
    )

    help_output = await _probe(binary_path=binary_path, args=contract.help_args)
    if help_output is None:
        return CliSurfaceReport(
            provider=provider,
            contract=contract,
            binary_path=binary_path,
            version=version,
            help_readable=False,
        )

    lowered = help_output.lower()
    return CliSurfaceReport(
        provider=provider,
        contract=contract,
        binary_path=binary_path,
        version=version,
        help_readable=True,
        missing_required_flags=tuple(
            flag for flag in contract.required_flags if not flag_named_in(lowered, flag)
        ),
        unadvertised_optional_flags=tuple(
            flag
            for flag in contract.optional_flag_names
            if not flag_named_in(lowered, flag)
        ),
    )


def probe_cli_surface_sync(*, provider: AIProvider) -> CliSurfaceReport:
    """Probe one provider's agent CLI from synchronous code.

    Args:
        provider: Provider whose declared CLI contract should be verified.

    Returns:
        The surface report.
    """
    return asyncio.run(probe_cli_surface(provider=provider))


def declared_cli_providers() -> tuple[AIProvider, ...]:
    """Return every provider that declares a CLI contract.

    Returns:
        Providers in declaration order, so a newly declared contract is picked up
        by the contract tests without editing them.
    """
    return tuple(CLI_CONTRACTS)
