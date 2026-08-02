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
    "prune_state_to_fit",
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

    Args:
        body: Existing sticky comment body.

    Returns:
        The decoded mapping, or ``None`` when absent or unparsable.
    """
    start = body.find(STATE_MARKER_PREFIX)
    if start < 0:
        return None
    after = body[start + len(STATE_MARKER_PREFIX) :]
    end = after.rfind(STATE_MARKER_SUFFIX)
    raw = after[:end] if end >= 0 else after
    try:
        payload = json.loads(raw.strip())
    except (json.JSONDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


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


def _migrate_v1(*, runs: list[RunRecord]) -> list[RunRecord]:
    """Stamp sequential round numbers onto migrated v1 run records.

    v1 stored no round number, so the position in the (chronological) run list
    is the only available ordering signal.

    Args:
        runs: Runs parsed from a v1 blob, oldest first.

    Returns:
        The same runs with 1-based round numbers applied.
    """
    return [replace(run, round=index) for index, run in enumerate(runs, start=1)]


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

    try:
        version = int(payload.get("version", STATE_VERSION_V1))
    except (TypeError, ValueError):
        return ReviewState()

    if version == STATE_VERSION_V1:
        return ReviewState(
            version=STATE_VERSION,
            runs=tuple(_migrate_v1(runs=_parse_runs(payload=payload))),
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
