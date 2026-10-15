"""The provenance fields one review round stamps on its metadata (#2867).

ADR-0011 decision B: a run record reports where its requests went and what
answered, not what was configured. :func:`stamp_run_provenance` reads the
environment the run was started in and the ledger its provider calls
reported to, and writes the answer onto the result's metadata.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from lintro.ai.provenance import ModelLedger, detect_run_provenance, verify_models
from lintro.ai.transport import cli_bare_billing

if TYPE_CHECKING:
    from lintro.ai.review.models.review_result import ReviewResult
    from lintro.ai.review.session import ReviewSessionOptions

__all__ = ["run_provenance_fields", "stamp_run_provenance"]


def stamp_run_provenance(
    *,
    result: ReviewResult,
    options: ReviewSessionOptions,
    ledger: ModelLedger | None,
) -> ReviewResult:
    """Return *result* with the round's provenance on its metadata.

    Args:
        result: The assembled review result.
        options: Session options naming the provider and transport.
        ledger: What the run's provider calls reported, or ``None`` when no
            ledger was open.

    Returns:
        The result, its metadata carrying the decision-B fields.
    """
    return replace(
        result,
        metadata=replace(
            result.metadata,
            **run_provenance_fields(options=options, ledger=ledger),
        ),
    )


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
        verification state, the lane and whether a cost was reported.
    """
    workspace = options.workspace_root
    provenance = detect_run_provenance(
        provider=options.provider.name,
        transport=options.ai_config.transport,
        bare=cli_bare_billing(options.ai_config),
        cwd=str(workspace) if workspace is not None else None,
    )
    calls = ledger.calls if ledger is not None else []
    verification = verify_models(
        requested=options.provider.model_name,
        calls=calls,
        first_party=provenance.first_party,
        allowed=provenance.allowed_models,
    )
    return {
        "endpoint": provenance.endpoint,
        "first_party": provenance.first_party,
        "credential": provenance.credential.value,
        "reported_models": ledger.reported_models if ledger is not None else (),
        "model_verification": verification.value if verification is not None else "",
        "lane": provenance.lane,
        "cost_reported": ledger.cost_reported if ledger is not None else False,
    }
