"""Declared CLI contracts for AI CLI transports.

lintro's ``--transport cli`` providers shell out to fast-moving external agent
binaries (``claude``, cursor ``agent``, ``codex``). Those binaries change their
flag surface between releases, and a removed flag breaks every review at
runtime (see #1611, where ``@anthropic-ai/claude-code`` 2.1.218 dropped
``--json-schema-name``).

This module holds the shared vocabulary for what lintro sends to each binary
and what it requires of it. The contracts themselves are declared per provider
(``lintro/ai/providers/<name>/cli_contract.py``) and reached through the
provider's :class:`~lintro.ai.providers.protocol.ProviderMetadata`, so a
vendor's CLI identity and its flag contract cannot drift apart (#2308):

* ``required_flags`` -- flags lintro cannot work without. They are not gated at
  runtime (silently dropping them would hang or badly degrade a call); instead
  the contract test asserts each one is still advertised by the installed
  binary, so drift breaks CI rather than a user's review.
* ``optional_flags`` -- flags lintro degrades gracefully without. These are
  gated by the capability guard's
  :meth:`~lintro.ai.providers.cli_capabilities.CliCapabilityGuard.supports_flag`
  before being sent and dropped-and-retried if the binary rejects them.
* ``version_floor`` -- a *known-incompatible-below* floor, not a known-good
  pin. It is deliberately conservative: binaries below it predate the flag
  surface lintro relies on, so an actionable error beats a confusing failure.
  Tightening floors to the pinned known-good versions is tracked separately
  under the baked ``lintro-ai-tools`` image work.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from lintro.ai.provider_enum import AIProvider
from lintro.ai.registry import all_metadata, metadata_for

__all__ = [
    "CliContract",
    "OptionalCliFlag",
    "cli_contract_for",
    "cli_contracts",
    "flag_named_in",
    "format_version",
    "unadvertised_flags",
]

# Flag characters that continue a flag token. Used to bound flag matches in CLI
# output so ``--foo`` does not match inside ``--foobar``.
_FLAG_CHAR = r"[0-9a-z-]"


def flag_named_in(lowered_text: str, flag: str) -> bool:
    """Return whether *flag* is named in *lowered_text* as a whole token.

    A plain substring test would let a rejection of ``--foobar`` also match a
    candidate ``--foo``, dropping the wrong optional flag. Matching on flag-token
    boundaries keeps the backstop precise even if a future contract adds flags
    that share a prefix.

    Args:
        lowered_text: Already-lowercased CLI output. Either a rejection on stderr
            or ``--help`` text: the runtime guard and the contract gate share
            this matcher precisely so both read a flag the same way.
        flag: The candidate flag, e.g. ``--resume``.

    Returns:
        True when the flag appears as a complete token.
    """
    pattern = rf"(?<!{_FLAG_CHAR}){re.escape(flag.lower())}(?!{_FLAG_CHAR})"
    return re.search(pattern, lowered_text) is not None


@dataclass(frozen=True, slots=True)
class OptionalCliFlag:
    """An optional CLI flag that lintro can run without.

    Attributes:
        flag (str): The literal flag as passed on the command line.
        purpose (str): Short description of what is lost when the flag is
            dropped.
    """

    flag: str
    purpose: str


@dataclass(frozen=True, slots=True)
class CliContract:
    """The flag surface and version floor lintro expects of one agent CLI.

    Attributes:
        binary (str): Executable name looked up on ``PATH``.
        display_name (str): Human-readable name used in log and error messages.
        upgrade_hint (str): Actionable guidance shown when the floor is not met.
        version_args (tuple[str, ...]): Argv suffix that prints the version.
        help_args (tuple[str, ...]): Argv suffix that prints the help text
            carrying the flag surface lintro uses. Sub-command CLIs
            (``codex exec``) must point at the sub-command's help, not the
            top-level one.
        version_floor (tuple[int, ...] | None): Lowest version lintro supports,
            as a component tuple, or ``None`` when no floor is declared.
        required_flags (tuple[str, ...]): Flags lintro always sends and cannot
            degrade without.
        optional_flags (tuple[OptionalCliFlag, ...]): Flags gated by capability
            detection.
    """

    binary: str
    display_name: str
    upgrade_hint: str
    version_args: tuple[str, ...] = ("--version",)
    help_args: tuple[str, ...] = ("--help",)
    version_floor: tuple[int, ...] | None = None
    required_flags: tuple[str, ...] = field(default=())
    optional_flags: tuple[OptionalCliFlag, ...] = field(default=())

    @property
    def optional_flag_names(self) -> tuple[str, ...]:
        """Return the bare flag names of every declared optional flag.

        Returns:
            Tuple of optional flag strings.
        """
        return tuple(item.flag for item in self.optional_flags)


def unadvertised_flags(
    *,
    lowered_help: str,
    flags: tuple[str, ...],
) -> tuple[str, ...]:
    """Return the flags *lowered_help* does not advertise, in declaration order.

    Shared by the runtime guard and the contract gate on purpose. The gate exists
    to certify what the guard relies on, so if the two ever matched flags
    differently the gate could pass a binary the guard then fails on. One helper
    makes that divergence impossible rather than merely unlikely.

    Args:
        lowered_help: Already-lowercased help output from the binary.
        flags: Declared flag names to look for.

    Returns:
        The subset of *flags* not present as whole tokens.
    """
    return tuple(flag for flag in flags if not flag_named_in(lowered_help, flag))


def format_version(version: tuple[int, ...] | None) -> str:
    """Render a parsed version tuple for display.

    Args:
        version: Parsed version components, or ``None``.

    Returns:
        Dotted version string, or ``"unknown"`` when *version* is ``None``.
    """
    if version is None:
        return "unknown"
    return ".".join(str(part) for part in version)


def cli_contract_for(provider: AIProvider) -> CliContract:
    """Return the declared CLI contract for *provider*.

    Args:
        provider: The provider whose CLI contract is requested.

    Returns:
        The provider's :class:`CliContract`.

    Raises:
        KeyError: If *provider* declares no CLI contract. Callers that cannot
            assume one iterate :func:`cli_contracts` instead.
    """
    contract = metadata_for(provider).cli_contract
    if contract is None:
        raise KeyError(provider)
    return contract


def cli_contracts() -> Mapping[AIProvider, CliContract]:
    """Return every declared CLI contract, keyed by provider.

    Built from plugin metadata on each call rather than cached in a module
    global: a cached copy is exactly the parallel table #2308 removed, and the
    lookup only walks three in-memory records.

    Returns:
        A read-only mapping in :class:`~lintro.ai.provider_enum.AIProvider`
        declaration order, holding only providers that declare a contract.
    """
    return MappingProxyType(
        {
            provider: metadata.cli_contract
            for provider, metadata in all_metadata().items()
            if metadata.cli_contract is not None
        },
    )
