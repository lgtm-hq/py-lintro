"""Encode, decode, and prune the hidden review-state blob.

The blob lives in the sticky PR comment as an HTML comment so GitHub renders
nothing while later rounds can recover the full history. Schema v2 adds
per-round statistics and per-finding identity on top of v1's run aggregates;
schema v3 adds the per-round convergence score and the per-finding evidence
style it is derived from (#2099). v2 blobs are read as unscored v3 history;
every other version — v1 and anything newer than this build knows — decodes as
no state at all rather than crashing a review run.

v1 is deliberately not migrated (#2305). Its aggregates carried no round
numbers and no finding identity, so reconstructing them meant guessing from
list position; sticky state v2 shipped in #1916 and every open pull request
has been re-reviewed since, which leaves the guess with nothing left to
recover. A v1 blob is therefore treated as absent and the next round starts a
fresh history.

Both v2 -> v3 additions are optional keys that the record serializers omit
when unset, so migrating a v2 blob emits no v3 keys: every run and finding parses
with its new field defaulted, and re-encoding a blob that carries no scores
writes the same record fields v2 wrote, with only the version restamped. The
migrated state is simply unscored history, which
:mod:`lintro.ai.review.convergence` treats as "not measured" rather than as
evidence of a quiet round.
"""

from __future__ import annotations

import json
from typing import Any

from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.github_constants import (
    GITHUB_COMMENT_HARD_LIMIT,
    STATE_MARKER_PREFIX,
    STATE_MARKER_SUFFIX,
    STATE_VERSION,
    STATE_VERSION_V2,
)
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_record import RunRecord

__all__ = [
    "decode_state",
    "encode_state",
    "leftover_state_block",
    "prune_state_to_fit",
    "render_state_block",
]


def encode_state(*, state: ReviewState) -> str:
    """Serialize a state object to its JSON payload.

    Args:
        state: State to serialize.

    Returns:
        Compact JSON string. The stored version is always the current schema
        version, so a v2 blob is written back as v3.
    """
    payload = state.to_dict()
    payload["version"] = STATE_VERSION
    return json.dumps(payload, separators=(",", ":"))


def render_state_block(*, state: ReviewState) -> str:
    """Render the hidden HTML-comment state block appended to the sticky body.

    Args:
        state: State to embed.

    Returns:
        Empty string. Authoritative state lives in workflow artifacts
        (#2154); the sticky is pure rendering and never writes a leftover
        blob.
    """
    del state
    return ""


def leftover_state_block(*, state: ReviewState) -> str:
    """Wrap encoded state in leftover-blob markers for decode and prune tests.

    New stickies never call this: #2154 moved authoritative state to workflow
    artifacts. The decoder still reads a v2 blob left behind on an older
    comment, and pruning has to measure the block it would produce, so the
    renderer stays where both can reach it.

    Args:
        state: State to encode.

    Returns:
        The historical HTML-comment block, including its leading blank line.
    """
    encoded = encode_state(state=state)
    return f"\n\n{STATE_MARKER_PREFIX} {encoded} {STATE_MARKER_SUFFIX}"


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

    # Require a genuine int. ``bool`` is an int subclass, so ``True`` would
    # otherwise compare equal to a version number, and a float like 2.9 would
    # read as a version that was never actually written. A missing key is the
    # unversioned v1 shape and fails the same check.
    version = payload.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        return ReviewState()

    if version not in (STATE_VERSION_V2, STATE_VERSION):
        # Two cases, one answer. A v1 or unversioned blob predates round
        # numbers and finding identity, and #2305 retired its migration, so it
        # is read as absent. A blob written by a newer lintro is
        # forward-incompatible, and starting fresh beats guessing at unknown
        # semantics.
        return ReviewState()

    # v2 -> v3 needs no field rewriting: the record parsers default the two
    # v3 additions, so a v2 blob decodes as unscored v3 history.
    return ReviewState(
        version=STATE_VERSION,
        runs=tuple(_parse_runs(payload=payload)),
        findings=tuple(_parse_findings(payload=payload)),
        truncated=bool(payload.get("truncated", False)),
    )


def _block_length(*, state: ReviewState) -> int:
    """Return the encoded leftover-blob length used by prune."""
    return len(leftover_state_block(state=state))


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
