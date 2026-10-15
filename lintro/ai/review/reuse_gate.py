"""Which stored rounds this run may inherit coverage from (#2867).

ADR-0011 decision B: review-reuse validity includes the lane. A stored round
is not reused when it predates the ADR (``legacy``), when its model claim
was a ``mismatch``, when it is ``unreported`` on a lane whose transport is
declared to report model usage, or when its lane or endpoint differ from the
current run's. Before coverage is inherited, every stored round is also
re-checked against the current lane row: on a first-party row its reported
models must still be allowed. A round that fails is re-reviewed rather than
carried into a green check; its history and cost figures are kept.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from lintro.ai.enums.model_verification import ModelVerification
from lintro.ai.provenance import RunProvenance, detect_run_provenance
from lintro.ai.transport import cli_bare_billing

if TYPE_CHECKING:
    from lintro.ai.review.models.review_state import ReviewState
    from lintro.ai.review.models.run_record import RunRecord
    from lintro.ai.review.session import ReviewSessionOptions

__all__ = [
    "INHERITED_EVIDENCE_STATES",
    "agent_model_overrides",
    "allowed_models_for",
    "reusable_rounds",
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
        "",
        ModelVerification.VERIFIED.value,
        ModelVerification.UNVERIFIABLE.value,
        ModelVerification.UNREPORTED.value,
    },
)


def _normalize(name: str) -> str:
    """Normalise a model name for comparison."""
    return name.strip().lower()


def _round_is_reusable(
    *,
    record: RunRecord,
    provenance: RunProvenance,
    allowed: frozenset[str],
) -> bool:
    """Return whether one stored round passes every reuse rule."""
    identity = record.identity
    state = identity.model_verification
    if state not in _REUSABLE_STATES:
        return False
    if state == ModelVerification.UNREPORTED.value and provenance.reports_model_usage:
        return False
    if identity.lane != provenance.lane or identity.endpoint != provenance.endpoint:
        return False
    if provenance.first_party:
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
    prior = options.prior_state
    if prior is None:
        return None
    if not prior.runs:
        return frozenset()
    workspace = options.workspace_root
    cwd = str(workspace) if workspace is not None else None
    provenance = detect_run_provenance(
        provider=options.provider.name,
        transport=options.ai_config.transport,
        bare=cli_bare_billing(options.ai_config, cwd=cwd),
        cwd=cwd,
        configured_base_url=options.ai_config.api_base_url,
    )
    return reusable_rounds(
        prior=options.prior_state,
        provenance=provenance,
        allowed=allowed_models_for(
            provenance=provenance,
            requested=options.provider.model_name,
            fallback_models=options.ai_config.fallback_models,
            agent_models=agent_model_overrides(options),
        ),
    )
