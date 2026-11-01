"""Which stored rounds this run may inherit coverage from (#2867).

ADR-0011 decision B: review-reuse validity includes the lane. A stored round
is not reused when its evidence is blank, when it predates the ADR
(``legacy``), when its model claim
was a ``mismatch``, when it is ``unreported`` on a lane whose transport is
declared to report model usage, or when its lane or endpoint differ from the
current run's, or when its state or first-party flag contradict the lane.
Before coverage is inherited, every stored round is also re-checked against
the current lane row: on a first-party row its reported models and silent
requested-model claims must still be allowed. A mixed unreported record
without its silent claims cannot vouch for coverage. A round that fails is
re-reviewed rather than carried into a green check; its history and cost
figures are kept.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from lintro.ai.enums.model_verification import ModelVerification
from lintro.ai.provenance import RunProvenance, detect_run_provenance
from lintro.ai.transport import cli_bare_billing, cli_runtime_env

if TYPE_CHECKING:
    from pathlib import Path

    from lintro.ai.config import AIConfig
    from lintro.ai.review.models.review_state import ReviewState
    from lintro.ai.review.models.run_identity import RunIdentity
    from lintro.ai.review.models.run_record import RunRecord
    from lintro.ai.review.session import ReviewSessionOptions

__all__ = [
    "INHERITED_EVIDENCE_STATES",
    "agent_model_overrides",
    "evidence_carries_over",
    "allowed_models_for",
    "reusable_rounds",
    "reusable_rounds_before_provider",
    "reusable_rounds_for",
]

#: Checkpoint states whose coverage a resumed attempt may have inherited, and
#: whose model evidence therefore stays on the round's record when a later
#: checkpoint or the final record replaces it.
INHERITED_EVIDENCE_STATES: frozenset[str] = frozenset(
    {ModelVerification.VERIFIED.value, ModelVerification.UNVERIFIABLE.value},
)


def allowed_models_for(
    *,
    provenance: RunProvenance,
    requested: str,
    fallback_models: Iterable[str] = (),
    agent_models: Iterable[str] = (),
) -> tuple[str, ...]:
    """Return the models this run may request or be answered by.

    Args:
        provenance: The run's provenance; its lane row's allowed list wins.
        requested: The run's default model.
        fallback_models: Declared fallbacks, allowed outside a lane row.
        agent_models: Custom-agent ``model`` overrides, allowed outside a
            lane row.

    Returns:
        The lane row's allowed models, or outside CI the default model, its
        declared fallbacks and the custom agents' overrides.
    """
    if provenance.allowed_models:
        return provenance.allowed_models
    names: dict[str, None] = {requested: None}
    for name in (*fallback_models, *agent_models):
        if name.strip():
            names.setdefault(name, None)
    return tuple(names)


def agent_model_overrides(options: ReviewSessionOptions) -> tuple[str, ...]:
    """Return the distinct ``model`` overrides the run's custom agents declare.

    Args:
        options: The run's session options.

    Returns:
        The overrides, in agent order, without repeats.
    """
    seen: dict[str, None] = {}
    for agent in options.custom_agents:
        if agent.model:
            seen.setdefault(agent.model, None)
    return tuple(seen)


#: Stored states a round may carry and still vouch for its coverage. Anything
#: else, including a value this build does not recognise, is excluded: a
#: corrupt or newer state means more review, not less.
_REUSABLE_STATES = frozenset(
    {
        ModelVerification.VERIFIED.value,
        ModelVerification.UNVERIFIABLE.value,
        ModelVerification.UNREPORTED.value,
    },
)


def _normalize(name: str) -> str:
    """Normalise a model name for comparison."""
    return name.strip().lower()


def evidence_carries_over(*, earlier: RunIdentity, current: RunIdentity) -> bool:
    """Return whether an earlier attempt's reported models stay on the record.

    They stay only when that attempt's coverage could have been inherited:
    its state is one the gate accepts and it ran on the same lane and
    endpoint as the attempt replacing it (#2867).

    Args:
        earlier: Identity of the checkpoint record being replaced.
        current: Identity of the record replacing it.

    Returns:
        True when the earlier attempt's models belong on the new record.
    """
    return (
        earlier.model_verification in INHERITED_EVIDENCE_STATES
        and earlier.lane == current.lane
        and earlier.endpoint == current.endpoint
    )


def _round_is_reusable(
    *,
    record: RunRecord,
    provenance: RunProvenance,
    allowed: frozenset[str],
) -> bool:
    """Return whether one stored round passes every reuse rule."""
    identity = record.identity
    state = identity.model_verification
    # No completed call means no evidence to vouch for coverage, even at
    # zero usage. Re-review rather than promote a blank state (#3102).
    if state not in _REUSABLE_STATES:
        return False
    if provenance.lane:
        states = (
            {ModelVerification.VERIFIED.value, ModelVerification.UNREPORTED.value}
            if provenance.first_party
            else {ModelVerification.UNVERIFIABLE.value}
        )
        if identity.first_party is not provenance.first_party or state not in states:
            return False
    if state == ModelVerification.UNREPORTED.value and provenance.reports_model_usage:
        return False
    if identity.lane != provenance.lane or identity.endpoint != provenance.endpoint:
        return False
    if provenance.first_party:
        if (
            state == ModelVerification.UNREPORTED.value
            and identity.reported_models
            and not identity.unreported_models
        ):
            # Older mixed aggregates lost silent sources' requests (#3102).
            # Their current identity.model cannot reconstruct those claims.
            return False
        if any(
            _normalize(model) not in allowed for model in identity.unreported_models
        ):
            return False
        # A record that reported no models is vouched for by what it asked
        # for, so a row that has since narrowed its list cannot be met
        # vacuously.
        if not identity.reported_models:
            return _normalize(identity.model) in allowed
        return all(
            _normalize(model.name) in allowed for model in identity.reported_models
        )
    return True


def reusable_rounds(
    *,
    prior: ReviewState | None,
    provenance: RunProvenance,
    allowed: Iterable[str],
) -> frozenset[int]:
    """Return the round numbers whose coverage this run may inherit.

    Args:
        prior: The PR's stored state, or ``None`` on a first round.
        provenance: The current run's provenance.
        allowed: The models the current lane allows.

    Returns:
        Round numbers of the stored runs that pass every rule. A coverage
        record whose run is no longer stored has nothing to vouch for it and
        is not inherited: missing state fails toward more review (ADR-0007).
    """
    if prior is None:
        return frozenset()
    allowed_names = frozenset(_normalize(name) for name in allowed if name.strip())
    return frozenset(
        record.identity.round
        for record in prior.runs
        if _round_is_reusable(
            record=record,
            provenance=provenance,
            allowed=allowed_names,
        )
    )


def reusable_rounds_for(*, options: ReviewSessionOptions) -> frozenset[int] | None:
    """Resolve the reusable rounds for a run from its session options.

    Args:
        options: The run's session options, carrying the prior state, the
            provider and the AI configuration.

    Returns:
        The reusable round numbers, or ``None`` when there is no prior state
        and nothing to gate. A prior that stores coverage but no run records
        cannot place that coverage on any lane, so none of it is inherited:
        such a state predates this gate or was hand-built, and missing
        provenance fails toward more review (ADR-0007, ADR-0011 B).
    """
    return reusable_rounds_before_provider(
        prior=options.prior_state,
        ai_config=options.ai_config,
        provider_name=options.provider.name,
        model_name=options.provider.model_name,
        agent_models=agent_model_overrides(options),
        workspace_root=options.workspace_root,
    )


def reusable_rounds_before_provider(
    *,
    prior: ReviewState | None,
    ai_config: AIConfig,
    provider_name: str,
    model_name: str,
    agent_models: Iterable[str] = (),
    workspace_root: Path | None = None,
) -> frozenset[int] | None:
    """Resolve the reusable rounds from what is known before a provider exists.

    The already-reviewed guard (#2338) runs before the provider is
    constructed, so it cannot hand over session options; it passes the
    resolved provider and model names instead. Custom-agent overrides may be
    omitted there: that only narrows the allowed list and so the inherited
    coverage, which fails toward review.

    Args:
        prior: The PR's stored state, or ``None`` on a first round.
        ai_config: The run's AI configuration.
        provider_name: The provider the run uses.
        model_name: The model the run requests.
        agent_models: Custom-agent ``model`` overrides, when known.
        workspace_root: Workspace the CLI subprocess runs in, for
            project-level settings files.

    Returns:
        The reusable round numbers, or ``None`` when there is no prior state
        and nothing to gate; an empty set for a prior without run records.
    """
    if prior is None:
        return None
    if not prior.runs:
        return frozenset()
    cwd = str(workspace_root) if workspace_root is not None else None
    # The environment the claude subprocess will run under (#2549), so the
    # credential and the bare-billing decision describe the run as made.
    env = cli_runtime_env(ai_config)
    provenance = detect_run_provenance(
        provider=provider_name,
        transport=ai_config.transport,
        bare=cli_bare_billing(ai_config, cwd=cwd, env=env),
        env=env,
        cwd=cwd,
        configured_base_url=ai_config.api_base_url,
    )
    return reusable_rounds(
        prior=prior,
        provenance=provenance,
        allowed=allowed_models_for(
            provenance=provenance,
            requested=model_name,
            fallback_models=ai_config.fallback_models,
            agent_models=agent_models,
        ),
    )
