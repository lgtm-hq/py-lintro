"""The provenance fields one review round stamps on its metadata (#2867).

ADR-0011 decision B: a run record reports where its requests went and what
answered, not what was configured. :func:`stamp_run_provenance` reads the
environment the run was started in and the ledger its provider calls
reported to, and writes the answer onto the result's metadata.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from lintro.ai.enums.model_verification import ModelVerification
from lintro.ai.provenance import (
    ModelLedger,
    RunProvenance,
    current_model_ledger,
    detect_run_provenance,
    verify_models,
)
from lintro.ai.review.coverage_rounds import newest_records_by_hash, own_records_at_hash
from lintro.ai.review.incremental_coverage import CheckpointStamp, LedgerSnapshot
from lintro.ai.review.models.run_identity import RunIdentity
from lintro.ai.review.reuse_gate import (
    agent_model_overrides,
    allowed_models_for,
    reusable_rounds_for,
)
from lintro.ai.transport import cli_bare_billing, cli_runtime_env

if TYPE_CHECKING:
    from lintro.ai.review.models.review_result import ReviewResult
    from lintro.ai.review.resume import ResumePlan
    from lintro.ai.review.session import ReviewSessionOptions

__all__ = [
    "checkpoint_stamp",
    "in_run_shadow_model",
    "run_provenance_fields",
    "stamp_run_provenance",
]


def in_run_shadow_model(*, options: ReviewSessionOptions) -> str:
    """Return the model that prices this run's calls, if it is off-host.

    Args:
        options: Session options naming the provider and transport.

    Returns:
        The lane row's shadow price model, or the requested model when the
        row names none, on a run off the provider's host; empty on a
        first-party run, whose figures stand as reported.
    """
    provenance = _provenance_for(options)
    if provenance.first_party:
        return ""
    return provenance.shadow_price_model or options.provider.model_name


def _provenance_for(options: ReviewSessionOptions) -> RunProvenance:
    """Detect the run's provenance from its session options."""
    workspace = options.workspace_root
    cwd = str(workspace) if workspace is not None else None
    # The environment the claude subprocess will run under (#2549), so the
    # credential and the bare-billing decision describe the run as made.
    env = cli_runtime_env(options.ai_config)
    return detect_run_provenance(
        provider=options.provider.name,
        transport=options.ai_config.transport,
        bare=cli_bare_billing(options.ai_config, cwd=cwd, env=env),
        env=env,
        cwd=cwd,
        configured_base_url=options.ai_config.api_base_url,
    )


def _allowed_for(
    options: ReviewSessionOptions,
    provenance: RunProvenance,
) -> tuple[str, ...]:
    """Return the models the run may request or be answered by."""
    return allowed_models_for(
        provenance=provenance,
        requested=options.provider.model_name,
        fallback_models=options.ai_config.fallback_models,
        agent_models=agent_model_overrides(options),
    )


def checkpoint_stamp(
    *,
    options: ReviewSessionOptions,
    cost_basis: str,
) -> CheckpointStamp:
    """Build what a mid-run checkpoint records about the round (#2867).

    A round killed after a checkpoint leaves coverage behind. The stamp's
    identity says which lane and endpoint produced it, and its verifier
    reads the open ledger at each checkpoint so the partial record carries
    the verification of the calls completed so far: a round interrupted
    after a mismatch is excluded from reuse like a finished one. The round
    number and the spend are filled in by the checkpoint writer.

    Args:
        options: Session options naming the provider and transport.
        cost_basis: The basis the transport profile resolved for the round.

    Returns:
        The stamp, less the PR number, round number and spend.
    """
    provenance = _provenance_for(options)
    allowed = _allowed_for(options, provenance)
    transport = options.ai_config.transport
    requested = options.provider.model_name

    def _verify() -> LedgerSnapshot:
        ledger = current_model_ledger()
        if ledger is None:
            return LedgerSnapshot()
        state = verify_models(
            requested=requested,
            calls=ledger.calls,
            first_party=provenance.first_party,
            allowed=allowed,
        )
        return LedgerSnapshot(
            state=state.value if state is not None else "",
            reported_models=ledger.reported_models,
            cost_reported=ledger.cost_reported,
        )

    return CheckpointStamp(
        identity=RunIdentity(
            model=requested,
            provider=str(options.provider.name),
            transport=transport.value if transport is not None else "",
            depth=options.depth,
            endpoint=provenance.endpoint,
            first_party=provenance.first_party,
            credential=provenance.credential.value,
            lane=provenance.lane,
            fallback_from=provenance.fallback_from,
            fallback_reason=provenance.fallback_reason,
        ),
        cost_basis=cost_basis,
        shadow_price_model=provenance.shadow_price_model or requested,
        verify=_verify,
    )


def stamp_run_provenance(
    *,
    result: ReviewResult,
    options: ReviewSessionOptions,
    ledger: ModelLedger | None,
    resume: ResumePlan | None = None,
) -> ReviewResult:
    """Return *result* with the round's provenance on its metadata.

    Args:
        result: The assembled review result.
        options: Session options naming the provider and transport.
        ledger: What the run's provider calls reported, or ``None`` when no
            ledger was open.
        resume: Gated coverage plan. A fully carried, no-call result keeps
            the actual evidence of the rounds covering its current hashes.

    Returns:
        The result, its metadata carrying the decision-B fields.
    """
    fields = run_provenance_fields(options=options, ledger=ledger)
    if not fields["model_verification"] and resume is not None:
        inherited = _carried_snapshot(result=result, options=options, resume=resume)
        if inherited is not None:
            fields.update(inherited)
    return replace(
        result,
        metadata=replace(
            result.metadata,
            **fields,
        ),
    )


def _carried_snapshot(
    *,
    result: ReviewResult,
    options: ReviewSessionOptions,
    resume: ResumePlan,
) -> dict[str, Any] | None:
    """Keep source evidence only when every current file was safely carried.

    No provider calls are fabricated and no tokens or charges are inherited.
    In particular ``unreported`` stays unreported on a silent transport; a
    configured model is never used to manufacture ``verified`` evidence.
    """
    prior = options.prior_state
    coverage = result.coverage
    if (
        prior is None
        or options.force_full
        or result.metadata.partial
        or coverage is None
        or not coverage.complete
        or not coverage.eligible
        or coverage.carried != coverage.eligible
        or coverage.reviewed
        or resume.queue
    ):
        return None
    reusable = reusable_rounds_for(options=options) or frozenset()
    runs = {run.identity.round: run.identity for run in prior.runs}
    own = own_records_at_hash(resume.coverage)
    newest = newest_records_by_hash(resume.coverage)
    identities: dict[int, RunIdentity] = {}
    for item in resume.classified:
        source = own.get((item.path, item.patch_hash)) or newest.get(item.patch_hash)
        # Defence in depth: the plan already contains only gated coverage.
        if source is None or source.round not in reusable or source.round not in runs:
            return None
        identities[source.round] = runs[source.round]
    if not identities:
        return None
    states = {identity.model_verification for identity in identities.values()}
    if len(states) > 1 and not states <= {
        ModelVerification.VERIFIED.value,
        ModelVerification.UNREPORTED.value,
    }:
        return None
    # Keep the weakest recorded state. The reuse gate has already excluded
    # missing, legacy and mismatched evidence and re-checked the lane/models.
    state = (
        ModelVerification.UNREPORTED.value
        if ModelVerification.UNREPORTED.value in states
        else next(iter(states))
    )
    reported = {
        model: None
        for identity in identities.values()
        for model in identity.reported_models
    }
    unreported = {
        model: None
        for identity in identities.values()
        for model in (
            identity.unreported_models
            or (
                (identity.model,)
                if identity.model_verification == ModelVerification.UNREPORTED.value
                else ()
            )
        )
    }
    return {
        "model_verification": state,
        "reported_models": tuple(reported),
        "unreported_models": tuple(unreported),
    }


def run_provenance_fields(
    *,
    options: ReviewSessionOptions,
    ledger: ModelLedger | None,
) -> dict[str, Any]:
    """Resolve the provenance fields one round's metadata carries.

    Args:
        options: Session options naming the provider and transport.
        ledger: What the run's provider calls reported, or ``None`` when no
            ledger was open.

    Returns:
        Keyword arguments for ``ReviewMetadata``: the endpoint host, whether
        it is first-party, the credential kind, the reported models, the
        verification state, the lane, the shadow price model and whether a
        cost was reported.
    """
    provenance = _provenance_for(options)
    calls = ledger.calls if ledger is not None else []
    verification = verify_models(
        requested=options.provider.model_name,
        calls=calls,
        first_party=provenance.first_party,
        allowed=_allowed_for(options, provenance),
    )
    return {
        "endpoint": provenance.endpoint,
        "first_party": provenance.first_party,
        "credential": provenance.credential.value,
        "reported_models": ledger.reported_models if ledger is not None else (),
        "unreported_models": tuple(
            dict.fromkeys(call.requested for call in calls if not call.reported),
        ),
        "model_verification": verification.value if verification is not None else "",
        "lane": provenance.lane,
        "fallback_from": provenance.fallback_from,
        "fallback_reason": provenance.fallback_reason,
        "shadow_price_model": provenance.shadow_price_model,
        "cost_reported": ledger.cost_reported if ledger is not None else False,
    }
