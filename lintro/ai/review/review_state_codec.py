"""Encode, decode, migrate, and prune the hidden review-state blob.

The blob lives in the sticky PR comment as an HTML comment so GitHub renders
nothing while later rounds can recover the full history. Schema v2 adds
per-round statistics and per-finding identity on top of v1's run aggregates;
v1 blobs are migrated on read and unknown versions start fresh rather than
crashing a review run.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.github_constants import (
    GITHUB_COMMENT_HARD_LIMIT,
    STATE_MARKER_PREFIX,
    STATE_MARKER_SUFFIX,
    STATE_VERSION,
    STATE_VERSION_V1,
)
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_record import RunRecord

__all__ = [
    "decode_state",
    "encode_state",
    "migrate_v1_runs",
    "prune_state_to_fit",
    "renumber_if_legacy_v1",
    "render_state_block",
]


def encode_state(*, state: ReviewState) -> str:
    """Serialize a state object to its JSON payload.

    Args:
        state: State to serialize.

    Returns:
        Compact JSON string. The stored version is always the current schema
        version, so a migrated v1 blob is written back as v2.
    """
    payload = state.to_dict()
    payload["version"] = STATE_VERSION
    return json.dumps(payload, separators=(",", ":"))


def render_state_block(*, state: ReviewState) -> str:
    """Render the hidden HTML-comment state block appended to the sticky body.

    Args:
        state: State to embed.

    Returns:
        The block, including its leading blank-line separator.
    """
    return (
        f"\n\n{STATE_MARKER_PREFIX} "
        + encode_state(state=state)
        + f" {STATE_MARKER_SUFFIX}"
    )


def _extract_payload(*, body: str) -> dict[str, Any] | None:
    """Pull the JSON mapping out of a sticky comment body.

    The authentic state block is always appended last. Model-derived finding
    text is not stripped of our marker, so a forged ``STATE_MARKER_PREFIX`` can
    appear earlier in the body — and, under schema v2, again as a substring
    inside the serialized finding title of the authentic block. Walk candidate
    markers from the end and accept the last one whose payload is a single
    JSON object closed by ``STATE_MARKER_SUFFIX`` (#1866).

    JSON string escaping defends the embedded-in-title case for any payload
    containing a double quote — the serialized title carries backslash-escaped
    quotes that fail ``raw_decode`` at that offset. The one quote-free object,
    ``{}``, would still decode, so payloads must also carry a known state key
    before they are accepted; anything else keeps walking.

    Args:
        body: Existing sticky comment body.

    Returns:
        The decoded mapping, or ``None`` when absent or unparsable.
    """
    decoder = json.JSONDecoder()
    search_end = len(body)
    while search_end > 0:
        start = body.rfind(STATE_MARKER_PREFIX, 0, search_end)
        if start < 0:
            return None
        after = body[start + len(STATE_MARKER_PREFIX) :].lstrip()
        try:
            payload, index = decoder.raw_decode(after)
        except (json.JSONDecodeError, ValueError):
            search_end = start
            continue
        rest = after[index:].lstrip()
        if not rest.startswith(STATE_MARKER_SUFFIX):
            search_end = start
            continue
        if isinstance(payload, dict) and _has_state_shape(payload=payload):
            return payload
        search_end = start
    return None


def _has_state_shape(*, payload: dict[str, Any]) -> bool:
    """Return whether a decoded payload looks like an authentic state blob.

    Args:
        payload: Candidate decoded JSON mapping.

    Returns:
        True when the mapping carries at least one known state key. Rejects
        ``{}`` — the only JSON object that survives inside a JSON-serialized
        finding title without escaped quotes — so an embedded forged marker
        cannot wipe the state (#1866).
    """
    return any(key in payload for key in ("version", "runs", "findings"))


def _parse_runs(*, payload: dict[str, Any]) -> list[RunRecord]:
    """Parse the run list from a decoded state payload."""
    runs = payload.get("runs")
    if not isinstance(runs, list):
        return []
    return [RunRecord.from_dict(entry) for entry in runs if isinstance(entry, dict)]


def _parse_findings(*, payload: dict[str, Any]) -> list[FindingRecord]:
    """Parse the finding list from a decoded state payload."""
    findings = payload.get("findings")
    if not isinstance(findings, list):
        return []
    parsed = (
        FindingRecord.from_dict(entry) for entry in findings if isinstance(entry, dict)
    )
    return [record for record in parsed if record is not None]


def migrate_v1_runs(*, runs: list[RunRecord]) -> list[RunRecord]:
    """Stamp sequential round numbers onto migrated v1 run records.

    v1 stored no round number, so the position in the (chronological) run list
    is the only available ordering signal. Shared by every entry point that
    can receive legacy v1 run data (the sticky comment path and the error
    comment path) so a given set of legacy runs always renumbers the same way
    regardless of which surface parsed them.

    Args:
        runs: Runs parsed from a v1 blob, oldest first.

    Returns:
        The same runs with 1-based round numbers applied.
    """
    return [replace(run, round=index) for index, run in enumerate(runs, start=1)]


def renumber_if_legacy_v1(*, runs: tuple[RunRecord, ...]) -> tuple[RunRecord, ...]:
    """Detect and renumber runs that look like an unversioned v1 blob.

    Callers that receive raw ``prior_runs`` mappings (rather than a full,
    versioned state blob) have no explicit version tag to dispatch on. A v1
    blob's runs all parse with the round-field default of ``1``, which is the
    only signal available; a single real round-1 run is indistinguishable
    from that and is left alone, since renumbering it would be a no-op. This
    single detection rule is shared by every such entry point (the sticky
    comment path and the error comment path) so a given set of legacy runs
    always renumbers the same way regardless of which surface parsed them.

    Args:
        runs: Runs already parsed via ``RunRecord.from_dict``.

    Returns:
        The renumbered runs when the v1 heuristic matches, otherwise ``runs``
        unchanged.
    """
    if runs and all(run.round == 1 for run in runs):
        return tuple(migrate_v1_runs(runs=list(runs)))
    return runs


def decode_state(*, body: str) -> ReviewState:
    """Decode the review state embedded in a sticky comment body.

    Args:
        body: Existing sticky comment body (may contain no state at all).

    Returns:
        The decoded state. A missing, malformed, or unknown-version blob yields
        an empty state rather than raising — a review run must never fail
        because of an unreadable comment.
    """
    payload = _extract_payload(body=body)
    if payload is None:
        return ReviewState()

    # Require a genuine int: bool is an int subclass (True/False would
    # otherwise silently decode as v1/v2), and a float like 2.9 would
    # truncate to a version that was never actually written.
    version = payload.get("version", STATE_VERSION_V1)
    if isinstance(version, bool) or not isinstance(version, int):
        return ReviewState()

    if version == STATE_VERSION_V1:
        return ReviewState(
            version=STATE_VERSION,
            runs=tuple(migrate_v1_runs(runs=_parse_runs(payload=payload))),
        )
    if version != STATE_VERSION:
        # Forward-incompatible blob written by a newer lintro: start fresh
        # instead of guessing at unknown semantics.
        return ReviewState()

    return ReviewState(
        version=STATE_VERSION,
        runs=tuple(_parse_runs(payload=payload)),
        findings=tuple(_parse_findings(payload=payload)),
        truncated=bool(payload.get("truncated", False)),
    )


def _block_length(*, state: ReviewState) -> int:
    """Return the rendered length of the state block for ``state``."""
    return len(render_state_block(state=state))


def prune_state_to_fit(
    *,
    state: ReviewState,
    body: str,
    limit: int = GITHUB_COMMENT_HARD_LIMIT,
) -> ReviewState:
    """Prune state until the body plus its state block fits GitHub's cap.

    Oldest run records are dropped first (the newest run is always kept), then
    resolved findings oldest-first, then open findings oldest-first. Any
    pruning sets :attr:`ReviewState.truncated` so downstream surfaces can say
    the history is incomplete.

    Args:
        state: State that would be embedded.
        body: Rendered sticky body *without* the state block.
        limit: Hard character limit for the whole comment.

    Returns:
        A state whose block fits the remaining budget, or the smallest state
        reachable by pruning when even that is impossible.
    """
    budget = limit - len(body)
    if _block_length(state=state) <= budget:
        return state

    runs = list(state.runs)
    findings = list(state.findings)

    def candidate_for(*, kept_findings: list[FindingRecord]) -> ReviewState:
        """Build the pruned candidate state for the current working lists."""
        return ReviewState(
            version=state.version,
            runs=tuple(runs),
            findings=tuple(kept_findings),
            truncated=True,
        )

    while len(runs) > 1:
        runs.pop(0)
        candidate = candidate_for(kept_findings=findings)
        if _block_length(state=candidate) <= budget:
            return candidate

    # Resolved findings go before open ones; within each group the oldest
    # (lowest ``since_round``) is dropped first.
    drop_order = sorted(
        range(len(findings)),
        key=lambda index: (
            findings[index].status is FindingStatus.OPEN,
            findings[index].since_round,
            index,
        ),
    )
    dropped: set[int] = set()
    for index in drop_order:
        dropped.add(index)
        kept = [
            record
            for position, record in enumerate(findings)
            if position not in dropped
        ]
        candidate = candidate_for(kept_findings=kept)
        if _block_length(state=candidate) <= budget:
            return candidate

    return candidate_for(kept_findings=[])
