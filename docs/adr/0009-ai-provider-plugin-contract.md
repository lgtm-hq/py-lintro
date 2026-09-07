# ADR-0009: AI provider plugin contract

## Status

Accepted

## Context

Lintro's AI backends are separated only at the class level.
`lintro/ai/providers/__init__.py` resolves a provider through a hardcoded
`provider_classes: dict[AIProvider, tuple[str, str]]` map plus a per-vendor `if`/`elif`
chain that assembles a different keyword list for each provider (`cli_bare` for
Anthropic, `cursor_trust_workspace` for Cursor). Adding a vendor means editing that
factory.

The metadata describing a provider is spread further still. `lintro/ai/registry.py`
holds default models, API-key environment variables and per-model pricing;
`lintro/ai/availability.py` holds the CLI binary names (`_CLI_BINARIES`);
`lintro/ai/providers/cli_contracts.py` holds the CLI flag surface and version floor;
`lintro/ai/cost.py` and `lintro/ai/display/status.py` read from those. Answering "what
does provider X support?" means visiting five modules.

Epic [#1999](https://github.com/lgtm-hq/py-lintro/issues/1999) replaces that with a
provider plugin model — register + package + metadata SSOT — delivered in five phases
scheduled by the roadmap ([#2288](https://github.com/lgtm-hq/py-lintro/issues/2288), row
A.8) as #2306-#2310. A behaviour-preserving migration needs the target seam written down
before any provider moves, so reviewers of the later phases can check each move against
a fixed contract rather than against an evolving one. That is this ADR and the two
modules it describes.

The contract is deliberately thinner than the tool plugin stack
(`lintro/plugins/protocol.py`, `@register_tool`). Tools run many at a time over files
and each needs a parser, an install entry and a dogfood manifest row. A provider is
"pick one backend for this call"; the review pipeline, the CLI transport and the
capability guard are shared horizontals that must not be duplicated per vendor.

## Decision

Lintro defines an in-tree provider plugin contract in `lintro/ai/providers/protocol.py`
and an in-tree registry in `lintro/ai/providers/registry.py`.

**1. `ProviderPlugin` is the seam.** A plugin exposes `name: AIProvider`,
`transports: frozenset[AITransport]`, `metadata: ProviderMetadata`, and
`build(config: AIConfig) -> BaseAIProvider`. The plugin reads the fields it needs off
the effective config, so the caller never assembles a per-vendor keyword list — the
per-provider `if`/`elif` in `get_provider` disappears into the plugin that owns those
knobs.

**2. Lifecycle is inherited, not redeclared.** `build` returns a `BaseAIProvider`, which
already carries `aclose`/`close` and the capability probes added for
[#1885](https://github.com/lgtm-hq/py-lintro/issues/1885) and the #1972 lifecycle work.
A plugin never reimplements teardown, and the registry never owns a provider's lifetime:
the caller that built a provider closes it.

**3. `ProviderMetadata` is the static description.** Every field is answerable without
importing the vendor SDK or spawning its CLI: default model, API-key environment
variable, SDK distribution, CLI binary, CLI contract id, and per-model pricing. That
keeps doctor, config validation, cost estimation and the CLI contract check on cheap
imports, and gives phase 3 (#2308) one destination to fold today's scattered tables
into.

**4. Discovery is in-tree registration only.** `register_provider` records a plugin
under its `name`; `get_registered` resolves one; `all_providers` returns them in
`AIProvider` declaration order so nothing depends on import order. Duplicate names raise
`AIProviderAlreadyRegisteredError` and unknown names raise
`AIProviderNotRegisteredError` — both subclasses of `AIError`, so an existing
`except AIError` boundary keeps working.

**5. Shared horizontals stay shared.** `cli_transport.py`, `cli_capabilities.py`,
`cli_contracts.py`, `call_ai`, the review orchestrator, redaction/transcript, and the
CLI and MCP adapters remain provider-agnostic and are not copied into plugins. This
restates, for the provider seam, the invariants ADR-0008 records for review.

**6. Nothing is migrated by the phase that introduces this contract.** The protocol, the
registry and this ADR land alone. `get_provider` keeps its class map until the migration
phase ([#2307](https://github.com/lgtm-hq/py-lintro/issues/2307)) moves Anthropic,
OpenAI and Cursor behind registration and deletes it. Until then the registry is empty
at runtime and exercised only by tests.

### Non-goals

Carried from the epic body, these are decisions not to do things, not omissions:

- **No entry-point or third-party out-of-tree providers in v1.** In-tree
  `register_provider` is enough. `PROVIDER_PLUGIN_API_VERSION` exists so a later
  entry-point loader has a compatibility handle, and nothing reads it today.
- **No per-vendor parser packages.** Providers return responses through the shared
  response/JSON handling; they do not each grow a `parsers/` package the way tools do.
- **No install-tools or manifest mirror per provider** unless a vendor genuinely needs
  one.
- **No duplication of `cli_transport`** into each plugin.
- **No rewrite of the finding model, sticky comments or prompt templates**, and no
  per-provider prompt divergence.
- **No requirement that every CI PR exercise every provider.** Dogfooding stays
  single-provider ([#1895](https://github.com/lgtm-hq/py-lintro/issues/1895)); the
  contract suite stays the multi-binary smoke path.
- **Not a module-size burn-down.** This is a seam, not an LOC split
  ([#1995](https://github.com/lgtm-hq/py-lintro/issues/1995) owns that).

## Consequences

Adding a vendor becomes: write a plugin, register it, add its tests. No central import
map, no factory edit, and the registry parity test fails if the plugin omits required
metadata or lifecycle.

The seam is fixed before any provider moves, so each later phase is reviewable as "does
this match ADR-0009?" rather than as a redesign. The cost is a period — the phase that
lands this ADR — where the contract exists and no provider satisfies it, and a second
definition of "provider metadata" (`ProviderMetadata`) coexisting with `ProviderInfo` in
`lintro/ai/registry.py` until #2308 folds one into the other. Both are deliberate and
bounded by the phase plan.

Because discovery is in-tree, a provider still has to be imported for its registration
to run; the migration phase owns choosing where those imports live. Revisit the
entry-point non-goal only if an out-of-tree vendor plugin is actually requested — that
is a later epic, not a stretch goal of this one.

## References

- Epic [#1999](https://github.com/lgtm-hq/py-lintro/issues/1999) — provider plugin model
- [#2306](https://github.com/lgtm-hq/py-lintro/issues/2306) — this contract and ADR
- Roadmap [#2288](https://github.com/lgtm-hq/py-lintro/issues/2288), row A.8
- `lintro/ai/providers/protocol.py`, `lintro/ai/providers/registry.py`
- `lintro/ai/providers/__init__.py` (`get_provider`), `lintro/ai/registry.py`,
  `lintro/ai/availability.py`, `lintro/ai/providers/cli_contracts.py`
- [ADR-0008](0008-ai-review-architecture-invariants.md) — AI review architecture
  invariants
- `lintro/plugins/protocol.py`, `lintro/plugins/registry.py` — tool plugin prior art
