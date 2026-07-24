"""Declared CLI contracts for AI CLI transports.

lintro's ``--transport cli`` providers shell out to fast-moving external agent
binaries (``claude``, cursor ``agent``, ``codex``). Those binaries change their
flag surface between releases, and a removed flag breaks every review at
runtime (see #1611, where ``@anthropic-ai/claude-code`` 2.1.218 dropped
``--json-schema-name``).

This module is the **single source of truth** for what lintro sends to each
binary and what it requires of it:

* ``required_flags`` -- flags lintro cannot work without. They are not gated at
  runtime (silently dropping them would hang or badly degrade a call); instead
  the contract test asserts each one is still advertised by the installed
  binary, so drift breaks CI rather than a user's review.
* ``optional_flags`` -- flags lintro degrades gracefully without. These are
  gated by :meth:`~lintro.ai.providers.cli_transport.CliTransport.supports_flag`
  before being sent and dropped-and-retried if the binary rejects them.
* ``version_floor`` -- a *known-incompatible-below* floor, not a known-good
  pin. It is deliberately conservative: binaries below it predate the flag
  surface lintro relies on, so an actionable error beats a confusing failure.
  Tightening floors to the pinned known-good versions is tracked separately
  under the baked ``lintro-ai-tools`` image work.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from lintro.ai.provider_enum import AIProvider

__all__ = [
    "CLI_CONTRACTS",
    "CliContract",
    "OptionalCliFlag",
    "cli_contract_for",
    "format_version",
]


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


_ANTHROPIC_CONTRACT = CliContract(
    binary="claude",
    display_name="Claude",
    upgrade_hint=(
        "Upgrade Claude Code: npm install -g @anthropic-ai/claude-code@latest"
    ),
    # Claude Code 2.x introduced the --bare / --json-schema surface lintro
    # drives; 1.x cannot serve a structured CLI review at all.
    version_floor=(2, 0, 0),
    required_flags=(
        "--bare",
        "--print",
        "--output-format",
        "--permission-mode",
        "--model",
        "--append-system-prompt",
        "--json-schema",
    ),
    optional_flags=(
        OptionalCliFlag(
            flag="--json-schema-name",
            purpose="names the structured-output schema",
        ),
        OptionalCliFlag(
            flag="--resume",
            purpose="reuses one CLI session across review turns",
        ),
    ),
)

_OPENAI_CONTRACT = CliContract(
    binary="codex",
    display_name="Codex",
    upgrade_hint="Upgrade Codex CLI: npm install -g @openai/codex@latest",
    # `codex exec --json` with structured output stabilised during 0.20.x.
    version_floor=(0, 20, 0),
    help_args=("exec", "--help"),
    required_flags=(
        "--json",
        "--sandbox",
        "--model",
    ),
    optional_flags=(
        OptionalCliFlag(
            flag="--output-schema",
            purpose="requests native structured output",
        ),
    ),
)

_CURSOR_CONTRACT = CliContract(
    binary="agent",
    display_name="Cursor agent",
    upgrade_hint=(
        "Upgrade the Cursor agent CLI: curl https://cursor.com/install -fsS | bash"
    ),
    # The agent CLI uses calendar versioning; every release carrying the
    # --print/--output-format surface lintro drives is 2025 or later.
    version_floor=(2025, 1, 1),
    required_flags=(
        "--print",
        "--output-format",
        "--mode",
        "--model",
        "--workspace",
    ),
    optional_flags=(
        OptionalCliFlag(
            flag="--trust",
            purpose="grants the agent workspace trust",
        ),
        OptionalCliFlag(
            flag="--resume",
            purpose="reuses one CLI session across review turns",
        ),
    ),
)

CLI_CONTRACTS: Mapping[AIProvider, CliContract] = MappingProxyType(
    {
        AIProvider.ANTHROPIC: _ANTHROPIC_CONTRACT,
        AIProvider.OPENAI: _OPENAI_CONTRACT,
        AIProvider.CURSOR: _CURSOR_CONTRACT,
    },
)


def cli_contract_for(provider: AIProvider) -> CliContract:
    """Return the declared CLI contract for *provider*.

    Args:
        provider: The provider whose CLI contract is requested.

    Returns:
        The provider's :class:`CliContract`.
    """
    return CLI_CONTRACTS[provider]
