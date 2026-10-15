"""Render where a review run went and what its figures mean (#2868, ADR-0011).

Decision B of ADR-0011 fixes two rendering rules. No surface prints the
configured model without the endpoint host and the verification state beside
it, because for months the sticky read ``claude-opus-5-5 (env)`` while a
third-party gateway answered. And every dollar figure carries its basis: a
figure on an ``unknown`` basis renders as "n/a (third-party gateway)", never
as dollars and never as zero; a list-price shadow is labelled a shadow; a
stored round from before the ADR is labelled as such.

The sticky header, the badges, the run mechanics footer, the cumulative
history and the terminal display all read from here, so the wording and the
rule live in one place.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from lintro.ai.cost import format_token_count
from lintro.ai.enums.cost_basis import CostBasis
from lintro.ai.enums.model_verification import ModelVerification
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.run_identity import RunIdentity
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.sanitize import sanitize_comment_text

__all__ = [
    "GATEWAY_COST_LABEL",
    "LEGACY_LABEL",
    "SHADOW_BASES",
    "ProvenanceView",
    "cost_label",
    "endpoint_note",
    "history_cost_summary",
    "model_label",
    "provenance_banner",
    "run_cost_figure",
    "terminal_cost_info",
    "view_from_identity",
    "view_from_metadata",
]

#: What an ``unknown``-basis figure renders as, wherever a cost is shown.
GATEWAY_COST_LABEL = "n/a (third-party gateway)"

#: Label for a stored round that predates ADR-0011 and names no endpoint.
LEGACY_LABEL = "predates ADR-0011"

#: Bases whose figure is a shadow for cap enforcement, never spend.
SHADOW_BASES = frozenset({CostBasis.LIST_PRICE_SHADOW.value, CostBasis.UNKNOWN.value})

#: Verification states that lead the sticky with a banner.
_BANNER_STATES = frozenset(
    {
        ModelVerification.MISMATCH.value,
        ModelVerification.UNVERIFIABLE.value,
        ModelVerification.UNREPORTED.value,
        ModelVerification.LEGACY.value,
    },
)


@dataclass(frozen=True)
class ProvenanceView:
    """The provenance facts every renderer reads, from metadata or a record.

    Attributes:
        model: The configured model, as requested.
        model_source: Where the model setting came from, or empty.
        endpoint: Host the transport was pointed at, or empty when unknown.
        first_party: Whether the endpoint is the provider's own; ``None``
            when nothing recorded it.
        lane: The review lane the CI wrapper exported, or empty.
        verification: The :class:`ModelVerification` value as stored, or
            empty for a round that completed no call.
        reported: Models the transport reported answering, canonical first.
    """

    model: str = ""
    model_source: str = ""
    endpoint: str = ""
    first_party: bool | None = None
    lane: str = ""
    verification: str = ""
    reported: tuple[str, ...] = ()

    @property
    def is_legacy(self) -> bool:
        """Return whether this is a stored round from before ADR-0011."""
        return self.verification == ModelVerification.LEGACY.value


def view_from_metadata(metadata: ReviewMetadata) -> ProvenanceView:
    """Build the view for the current round from its metadata.

    Args:
        metadata: Review run metadata.

    Returns:
        The provenance facts the renderers read.
    """
    return ProvenanceView(
        model=metadata.model,
        model_source=metadata.model_source,
        endpoint=metadata.endpoint,
        first_party=metadata.first_party,
        lane=metadata.lane,
        verification=metadata.model_verification,
        reported=tuple(m.canonical or m.model for m in metadata.reported_models),
    )


def view_from_identity(identity: RunIdentity) -> ProvenanceView:
    """Build the view for a stored round from its run identity.

    Args:
        identity: The stored run identity.

    Returns:
        The provenance facts the renderers read. A stored record from before
        ADR-0011 carries no endpoint and no state and reads as ``legacy``;
        a live round that recorded nothing is not a stored record and makes
        no such claim.
    """
    verification = identity.model_verification
    if not verification and not identity.endpoint:
        verification = ModelVerification.LEGACY.value
    return ProvenanceView(
        model=identity.model,
        endpoint=identity.endpoint,
        first_party=identity.first_party,
        lane=identity.lane,
        verification=verification,
        reported=tuple(m.canonical or m.model for m in identity.reported_models),
    )


def _code(text: str, *, limit: int, plain: bool) -> str:
    """Quote *text* as a code span unless rendering plain terminal text."""
    safe = sanitize_comment_text(text, limit=limit)
    return safe if plain else f"`{safe}`"


def _host(view: ProvenanceView, *, plain: bool) -> str:
    """Render the endpoint host, naming a gateway as one."""
    host = _code(view.endpoint, limit=80, plain=plain)
    if view.first_party is False:
        return f"{host} (third-party gateway)"
    return host


def endpoint_note(view: ProvenanceView, *, plain: bool = False) -> str:
    """Describe where the round went and whether the model claim held.

    Args:
        view: The round's provenance facts.
        plain: Render without Markdown code spans, for the terminal.

    Returns:
        A short clause, for example ``verified on `api.anthropic.com```,
        ``answered via `api.z.ai` (third-party gateway), model
        unverifiable``, or :data:`LEGACY_LABEL`; empty when the round made
        no call and recorded nothing.
    """
    if view.is_legacy:
        return LEGACY_LABEL
    state = view.verification
    if not state and not view.endpoint:
        # Nothing was recorded at all: there is nothing to stand beside.
        return ""
    if state == ModelVerification.VERIFIED.value:
        return f"verified on {_host(view, plain=plain)}"
    if state == ModelVerification.MISMATCH.value:
        reported = ", ".join(_code(m, limit=60, plain=plain) for m in view.reported)
        answered = f" (reported {reported})" if reported else ""
        return f"mismatch on {_host(view, plain=plain)}{answered}"
    if state == ModelVerification.UNVERIFIABLE.value:
        return f"answered via {_host(view, plain=plain)}, model unverifiable"
    if state == ModelVerification.UNREPORTED.value:
        return f"unverified by the transport on {_host(view, plain=plain)}"
    # An endpoint was recorded but no call completed: nothing to verify.
    return f"on {_host(view, plain=plain)}, no call completed"


def model_label(
    view: ProvenanceView,
    *,
    limit: int = 60,
    plain: bool = False,
) -> str:
    """Render the configured model with its endpoint beside it (ADR-0011 B).

    Args:
        view: The round's provenance facts.
        limit: Character cap applied to the model name.
        plain: Render without Markdown code spans, for the terminal.

    Returns:
        ``` `model` (source) · <endpoint note> ```; the note is never omitted.
    """
    model = _code(view.model or "unknown", limit=limit, plain=plain)
    if view.model_source:
        model = f"{model} ({sanitize_comment_text(view.model_source, limit=40)})"
    note = endpoint_note(view, plain=plain)
    return f"{model} · {note}" if note else model


def provenance_banner(view: ProvenanceView) -> str:
    """Render the line that leads the sticky when the model claim did not hold.

    It sits outside every collapsible section (ADR-0011 B). A ``mismatch``
    is a contradiction and reads as one; ``unreported`` on a transport that
    reports no model usage is informational and is not red.

    Args:
        view: The round's provenance facts.

    Returns:
        A blockquote, or an empty string when the state is ``verified`` or
        the round completed no call.
    """
    state = view.verification
    if state not in _BANNER_STATES:
        return ""
    lane = f"lane `{sanitize_comment_text(view.lane, limit=40)}`, " if view.lane else ""
    model = f"`{sanitize_comment_text(view.model or 'unknown', limit=60)}`"
    if state == ModelVerification.LEGACY.value:
        return (
            f"> ℹ️ **Provenance unknown** — this round {LEGACY_LABEL}: it records "
            f"no endpoint, so nothing can say which lane answered for {model}."
        )
    where = f"{lane}endpoint {_host(view, plain=False)}"
    if state == ModelVerification.MISMATCH.value:
        reported = ", ".join(
            f"`{sanitize_comment_text(m, limit=60)}`" for m in view.reported
        )
        answered = f" The transport reported {reported}." if reported else ""
        return (
            f"> ❌ **Model mismatch** — {where}: the requested model {model} "
            f"did not answer.{answered}"
        )
    if state == ModelVerification.UNVERIFIABLE.value:
        return (
            f"> ⚠️ **Model unverifiable** — {where}: the requested model {model} "
            "cannot be confirmed through a third-party gateway, and its cost is "
            "not priced."
        )
    return (
        f"> ℹ️ **Model unverified by the transport** — {where}: the transport "
        f"reported no model usage, so {model} is taken on trust."
    )


def run_cost_figure(run: RunRecord) -> float:
    """Return the figure a stored round shows: its spend, or its shadow.

    Args:
        run: The stored run record.

    Returns:
        ``usage.cost_shadow`` on a shadow basis, else ``usage.cost``.
    """
    if run.usage.cost_basis in SHADOW_BASES:
        return run.usage.cost_shadow
    return run.usage.cost


def cost_label(*, cost: float, basis: str, estimated: bool) -> str:
    """Render a dollar figure with its basis (ADR-0011 B).

    Args:
        cost: The figure: spend, or the shadow on a shadow basis.
        basis: The :class:`CostBasis` value, or empty for a legacy figure.
        estimated: Whether the figure was priced locally; sets the ``~``.

    Returns:
        ``n/a (third-party gateway)`` on ``unknown``; otherwise the figure
        with a label naming what it is.
    """
    if basis == CostBasis.UNKNOWN.value:
        return GATEWAY_COST_LABEL
    if basis == CostBasis.LIST_PRICE_SHADOW.value:
        return f"~${cost:.4f} (list-price shadow, billed to subscription)"
    if basis == CostBasis.BILLED.value:
        return f"${cost:.4f} (billed)"
    if basis == CostBasis.ESTIMATED.value:
        return f"~${cost:.4f} (estimated)"
    if basis == CostBasis.UNPRICEABLE.value:
        return f"~${cost:.4f} (local estimate, subscription)"
    prefix = "~" if estimated else ""
    return f"{prefix}${cost:.4f}"


def history_cost_summary(runs: Sequence[RunRecord]) -> str:
    """Summarise what a set of rounds cost, disclosing what was not priced.

    Args:
        runs: The stored rounds.

    Returns:
        The priced total, then the shadow total when any, then how many
        rounds contributed no priced cost; a complete-looking total is never
        shown over rounds the gateway left unpriced (ADR-0011 B).
    """
    priced = sum(run.usage.cost for run in runs)
    estimated = any(run.usage.estimated for run in runs)
    shadow = sum(
        run.usage.cost_shadow
        for run in runs
        if run.usage.cost_basis == CostBasis.LIST_PRICE_SHADOW.value
    )
    unpriced = sum(1 for run in runs if run.usage.cost_basis == CostBasis.UNKNOWN.value)
    parts = [f"{'~' if estimated else ''}${priced:.4f}"]
    if shadow:
        parts.append(f"${shadow:.4f} shadow")
    if unpriced:
        noun = "round" if unpriced == 1 else "rounds"
        parts.append(f"{unpriced} {noun} unpriced (gateway)")
    return " · ".join(parts)


def terminal_cost_info(metadata: ReviewMetadata) -> str:
    """Build the terminal header's cost summary with its basis.

    Args:
        metadata: Review run metadata.

    Returns:
        The tokens and the labelled figure, or an empty string when there is
        neither a figure nor a gateway to disclose.
    """
    tokens = format_token_count(
        int(metadata.token_usage.get("prompt", 0))
        + int(metadata.token_usage.get("completion", 0)),
    )
    if metadata.cost_basis == CostBasis.UNKNOWN.value:
        return f"   {tokens} tokens, cost {GATEWAY_COST_LABEL}"
    if metadata.cost_estimate_usd <= 0:
        return ""
    label = cost_label(
        cost=metadata.cost_estimate_usd,
        basis=metadata.cost_basis,
        estimated=metadata.token_usage_estimated,
    )
    return f"   {tokens} tokens, {label}"
