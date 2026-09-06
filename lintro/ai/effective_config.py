"""The one effective-AI-config resolver every surface uses (#2299).

Effective AI settings come into existence exactly once per invocation, here.
:func:`resolve_effective_ai_config` layers the raw project ``ai:`` mapping,
the six ``LINTRO_AI_*`` environment overlays, and the invocation's CLI flags
in the precedence ADR-0006 records (``flag > env > project > default``) and
returns a :class:`~lintro.ai.resolved_ai_config.ResolvedAIConfig` carrying
both the validated config and per-field provenance.

Every AI surface — ``check``/``fmt`` lint enhancement, ``lintro review``,
``lintro doctor``, the pre-execution status rows, MCP, and the advisory
tools — consumes that value. None of them re-parse the raw mapping and none
of them apply post-resolution overrides of their own: the ``--transport``
override that ``check``/``fmt`` used to apply as a separate post-resolution
``model_copy`` is now just another CLI overlay on this pipeline (epic #1972,
2026-08-14 owner comment item 1).

Cap monotonicity is per surface, not global (ADR-0008 invariant 6): CLI flags
and ``LINTRO_AI_*`` overlays may raise or lift ``ai.max_cost_usd``, while
MCP's per-call ``max_cost_usd`` argument stays a clamp that may only lower the
effective ceiling. That clamp lives in the MCP adapter
(:func:`lintro.mcp.toolkits.review.resolve_budget_policy`), downstream of this
resolver.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from lintro.ai.config import AIConfig
from lintro.ai.config_overrides import apply_cli_overrides
from lintro.ai.resolved_ai_config import ResolvedAIConfig

__all__ = [
    "NO_CLI_OVERRIDES",
    "AICliOverrides",
    "resolve_effective_ai_config",
]


@dataclass(frozen=True, slots=True)
class AICliOverrides:
    """Per-invocation CLI overrides for the five overridable ``ai:`` fields.

    Every field is ``None`` when its flag was not passed, which is what keeps
    "omitted" distinguishable from "set to the default". There is no
    ``enabled`` flag: the master switch comes from config or
    ``LINTRO_AI_ENABLED`` only.

    Attributes:
        provider: ``--provider`` value, or None when unset.
        model: ``--model`` value, or None when unset.
        transport: ``--transport`` value, or None when unset. ``check`` and
            ``fmt`` supply this too, so the lint path carries provenance.
        review: ``--review/--no-review`` value, or None when unset.
        max_cost_usd: ``--max-cost-usd`` value (``uncapped`` lifts the
            ceiling), or None when unset.
    """

    provider: str | None = None
    model: str | None = None
    transport: str | None = None
    review: bool | None = None
    max_cost_usd: float | str | None = None


#: Shared "no flags were passed" value, so surfaces without CLI overrides do
#: not each construct an empty one.
NO_CLI_OVERRIDES = AICliOverrides()


def resolve_effective_ai_config(
    mapping: Mapping[str, Any] | None,
    *,
    cli_overrides: AICliOverrides = NO_CLI_OVERRIDES,
    diagnostics: bool = True,
) -> ResolvedAIConfig:
    """Resolve the effective AI configuration for one invocation.

    This is the only production caller of
    :meth:`AIConfig.resolve_from_mapping`. The environment layer is read from
    ``os.environ`` inside that call rather than passed in, so a surface cannot
    accidentally resolve against a different environment than the one the run
    executes in.

    Args:
        mapping: Raw ``ai:`` section from the project config, or None when the
            config has no ``ai:`` block.
        cli_overrides: Flags passed on this invocation. Defaults to
            :data:`NO_CLI_OVERRIDES` for surfaces that have none.
        diagnostics: Whether this resolution may emit user-facing diagnostics
            (the dropped-unknown-key warning and the validators' migration
            hints). Display-only callers pass False so a summary never
            duplicates the execution path's output.

    An invalid environment or flag override raises
    :class:`~lintro.ai.exceptions.AIConfigOverrideError` from the layer that
    parses it, so a bad override fails at resolution and never falls through
    to the config default.

    Returns:
        The validated effective config together with provenance for
        ``provider``, ``model``, ``transport``, ``enabled``, ``review``, and
        ``max_cost_usd``.
    """
    resolved = AIConfig.resolve_from_mapping(mapping, diagnostics=diagnostics)
    return apply_cli_overrides(
        resolved,
        provider=cli_overrides.provider,
        model=cli_overrides.model,
        transport=cli_overrides.transport,
        review=cli_overrides.review,
        max_cost_usd=cli_overrides.max_cost_usd,
    )
