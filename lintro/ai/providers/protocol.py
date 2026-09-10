"""Provider plugin contract for AI backends.

Defines the seam every AI provider will be registered behind: a frozen
:class:`ProviderMetadata` record describing the vendor without importing its
SDK, and the :class:`ProviderPlugin` protocol that turns an
:class:`~lintro.ai.config.AIConfig` into a live
:class:`~lintro.ai.providers.base.BaseAIProvider`.

Every in-tree provider implements this contract:
:func:`lintro.ai.providers.get_provider` resolves through the plugin registry
(#2307), and since #2308 a plugin's
:class:`ProviderMetadata` is the only declaration of that vendor's defaults,
pricing, credentials and CLI identity. See
``docs/adr/0009-ai-provider-plugin-contract.md`` for the decision and the
explicit non-goals (no per-vendor parser packages, no install-tools mirror, no
entry-point discovery in v1).

Example:
    >>> from dataclasses import dataclass
    >>> from lintro.ai.enums import AITransport
    >>> from lintro.ai.provider_enum import AIProvider
    >>>
    >>> @dataclass(frozen=True, kw_only=True)
    ... class ExamplePlugin:
    ...     name: AIProvider = AIProvider.ANTHROPIC
    ...     transports: frozenset[AITransport] = frozenset({AITransport.API})
    ...     metadata: ProviderMetadata = ...
    ...
    ...     def build(self, config):  # -> BaseAIProvider
    ...         ...
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping

    from lintro.ai.config import AIConfig
    from lintro.ai.enums import AITransport
    from lintro.ai.model_pricing import ModelPricing
    from lintro.ai.provider_enum import AIProvider
    from lintro.ai.providers.base import BaseAIProvider
    from lintro.ai.providers.cli_auth_probe import CliAuthProbe
    from lintro.ai.providers.cli_contracts import CliContract

__all__ = [
    "PROVIDER_PLUGIN_API_VERSION",
    "ProviderMetadata",
    "ProviderPlugin",
]

#: Current major version of the in-tree provider plugin contract.
#:
#: Bump this only on a breaking change to :class:`ProviderPlugin` or
#: :class:`ProviderMetadata`. It exists so a future entry-point loader has a
#: compatibility handle to check; v1 discovery is in-tree only, so nothing
#: reads it yet.
PROVIDER_PLUGIN_API_VERSION: int = 1


@dataclass(frozen=True, kw_only=True, slots=True)
class ProviderMetadata:
    """Static description of one AI provider.

    Everything here is answerable without importing the vendor SDK or spawning
    its CLI, so doctor output, config validation, cost estimation, and the CLI
    contract check can read a plugin's metadata cheaply. Since #2308 this
    record is the *only* declaration of these facts: the parallel tables that
    lived in :mod:`lintro.ai.registry`, :mod:`lintro.ai.availability`,
    :mod:`lintro.ai.cost`, :mod:`lintro.ai.display.status` and
    :mod:`lintro.ai.providers.cli_contracts` are gone, and every one of those
    consumers now reads a plugin's metadata through
    :mod:`lintro.ai.registry`'s facade.

    Attributes:
        provider: Enum identity of the provider this metadata describes.
        display_name: Human-readable vendor name for prose and generated docs.
        default_model: Model identifier used when the user names none.
        default_api_key_env: Environment variable read for the API key.
        supported_transports: Transports this provider can actually serve. A
            transport absent from the set is a configuration error: doctor
            reports the pairing as incompatible, and the provider constructor
            rejects it rather than failing later inside the vendor call.
        default_transport: The transport this provider is documented and
            steered towards — what doctor tells a user to set, and what the
            generated provider table in ``docs/ai-features.md`` shows. Always
            a member of *supported_transports*. It is deliberately *not* the
            factory's unset-transport fallback: that fallback is ``api`` for
            every provider, including CLI-only ones, so an unset transport
            still produces the pre-plugin error text (see
            :meth:`lintro.ai.providers.cursor.plugin.CursorPlugin.build`).
        cli_default_model: Model sent on CLI transport when the user names none
            and the CLI is authenticated by a subscription session rather than
            an API key. ``None`` — the default, and what OpenAI declares —
            means send no model flag at all and let the binary pick its own,
            which is the only choice guaranteed to be one the plan offers
            (#2537). A provider whose subscription catalogue is stable may name
            a model here instead. Ignored by API transport, which always
            resolves to *default_model*.
        sdk_package: Distribution installed for API transport, or ``None``
            when the provider has no API transport (CLI-only vendors).
        cli_binary: Executable looked up on ``PATH`` for CLI transport, or
            ``None`` when the provider has no CLI transport.
        cli_contract_id: Stable string key for the provider's CLI contract, or
            ``None`` when it declares none. Kept a plain string so a caller
            that only needs the key never imports the contract definitions
            alongside *cli_contract*.
        cli_contract: The flag surface and version floor lintro expects of
            *cli_binary*, or ``None`` when the provider declares no CLI
            contract. Declared here so a provider's CLI identity and the
            contract it is checked against cannot drift apart.
        cli_install_hint: Actionable guidance doctor shows when *cli_binary* is
            not on ``PATH``.
        cli_auth_probe: How doctor decides, without spawning the binary,
            whether the CLI can authenticate.
        pricing: Known model identifiers mapped to their per-million-token
            pricing. Empty when the provider publishes no per-token price
            (for example a flat-rate subscription CLI).
    """

    provider: AIProvider
    display_name: str
    default_model: str
    default_api_key_env: str
    supported_transports: frozenset[AITransport]
    default_transport: AITransport
    cli_default_model: str | None = None
    sdk_package: str | None = None
    cli_binary: str | None = None
    cli_contract_id: str | None = None
    cli_contract: CliContract | None = None
    cli_install_hint: str | None = None
    cli_auth_probe: CliAuthProbe | None = None
    pricing: Mapping[str, ModelPricing] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Store *pricing* behind a read-only view and check the transports.

        A frozen dataclass stops the attribute being rebound but not the
        mapping being edited in place, and consumers treat metadata as a
        constant. Copying into a ``MappingProxyType`` also detaches the record
        from a caller that keeps mutating the dict it passed in.

        Raises:
            ValueError: If ``default_transport`` is not one of
                ``supported_transports``. Doctor and the generated provider
                table steer users towards this transport, so a default the
                provider cannot serve would advertise an unusable pairing. It
                is rejected where it is declared rather than surfacing as a
                confusing doctor hint. (It is not the factory's
                unset-transport fallback; see the field's own docs.)
        """
        object.__setattr__(self, "pricing", MappingProxyType(dict(self.pricing)))
        if self.default_transport not in self.supported_transports:
            supported = ", ".join(sorted(t.value for t in self.supported_transports))
            raise ValueError(
                f"Provider '{self.provider.value}' declares default transport "
                f"'{self.default_transport.value}', which is not among its "
                f"supported transports: {supported or 'none'}.",
            )

    @property
    def pricing_keys(self) -> tuple[str, ...]:
        """Return the model identifiers this provider publishes pricing for.

        Returns:
            Model identifiers in declaration order.
        """
        return tuple(self.pricing)

    def supports(self, transport: AITransport) -> bool:
        """Report whether this provider can serve *transport*.

        Args:
            transport: The transport to test.

        Returns:
            True when *transport* is one this provider serves.
        """
        return transport in self.supported_transports


@runtime_checkable
class ProviderPlugin(Protocol):
    """The contract an in-tree AI provider plugin satisfies.

    A plugin is a small value object that knows how to name, describe, and
    construct one vendor backend. It deliberately owns none of the shared
    horizontals: the CLI transport, the capability guard, ``call_ai``, the
    review orchestrator, and the MCP/CLI adapters stay shared and
    provider-agnostic.

    Lifecycle is inherited rather than redeclared: the object ``build``
    returns is a :class:`~lintro.ai.providers.base.BaseAIProvider`, so
    ``aclose`` / ``close`` and the capability probes already exist on it and a
    plugin never reimplements them.

    The three descriptive members are declared read-only so a plugin may be a
    frozen dataclass; nothing rebinds them after construction.
    """

    @property
    def name(self) -> AIProvider:
        """Return the enum identity used as the registry key.

        This is also the value users pass to ``--provider``.

        Returns:
            The provider this plugin builds.
        """
        ...  # pragma: no cover - protocol declaration

    @property
    def transports(self) -> frozenset[AITransport]:
        """Return the transports this provider can actually serve.

        A transport absent from the set is rejected at construction rather
        than failing later inside the vendor call.

        Returns:
            The supported transports.
        """
        ...  # pragma: no cover - protocol declaration

    @property
    def metadata(self) -> ProviderMetadata:
        """Return the static provider description.

        Returns:
            The metadata record; see :class:`ProviderMetadata`.
        """
        ...  # pragma: no cover - protocol declaration

    def build(self, config: AIConfig) -> BaseAIProvider:
        """Construct the provider instance described by *config*.

        The plugin reads whatever fields it needs off the effective config —
        including provider-specific knobs — so the caller never assembles a
        per-vendor keyword list. Transcript setup, workspace resolution, and
        every other cross-cutting concern stay with the caller.

        Args:
            config: Effective AI configuration for this run.

        Returns:
            A configured provider whose lifecycle the caller owns.
        """
        ...  # pragma: no cover - protocol declaration
