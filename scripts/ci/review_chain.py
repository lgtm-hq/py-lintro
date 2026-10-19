#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Decide whether the dogfood review walks to the next lane (ADR-0011 D, #2872).

``scripts/ci/run-ai-review.sh`` calls ``decide`` after each attempt. The
chain advances only when all of these hold:

- ``lintro review`` exited with the review error status (2);
- the last error envelope in the attempt's output names a kind in
  :data:`FALLBACK_KINDS`: exactly ``auth_failed``, ``insufficient_credits``
  and ``quota_exceeded``, the dead-credential or exhausted-account class. A
  rate limit, a server error or a timeout stays with the existing retry;
  ``provider_unavailable`` is a configuration failure. The decision keys on
  the kind, never on the envelope's ``provider_unavailable`` flag;
- no chunk call of this attempt completed. The envelope's
  ``completed_chunks`` is the evidence: lintro stamps it at the run boundary,
  so it is present for every failure the run raised, and a missing or
  non-zero count stops the walk. A checkpoint part (``part-NNNN.json``)
  newer than the attempt's start marker stops it too, as a second witness,
  but a part is best-effort and is never the only one;
- a lane is left in the chain.

A dead-lane kind on the last lane is ``exhausted``: nothing is left to walk
to, but the lane is recorded down like the others. Every other outcome is
``stop``, with the reason. ``lanes-down`` keeps the
JSON list of lanes that failed before any chunk completed, which the wrapper
exports as ``LINTRO_REVIEW_LANES_DOWN`` for the classifier's
``lanes-exhausted`` verdict and the fallback warning. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

__all__ = [
    "ADVANCE",
    "EXHAUSTED",
    "FALLBACK_KINDS",
    "REVIEW_STATUS_ERROR",
    "STOP",
    "decide",
    "lanes_down",
    "main",
]

#: The error kinds that walk the chain (ADR-0011 D). A strict subset of
#: ``lintro.ai.review.error_contract.UNAVAILABLE_KINDS``.
FALLBACK_KINDS: Final[frozenset[str]] = frozenset(
    {"auth_failed", "insufficient_credits", "quota_exceeded"},
)

#: ``lintro review``'s exit status for a review that could not be produced.
REVIEW_STATUS_ERROR: Final[int] = 2

#: The verbs ``decide`` prints; the wrapper matches on them.
ADVANCE: Final[str] = "advance"
EXHAUSTED: Final[str] = "exhausted"
STOP: Final[str] = "stop"

#: A lane id as the manifest resolver accepts it; anything else is refused
#: before it can reach an export or an annotation.
_LANE_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
#: An error kind as the taxonomy spells them.
_KIND_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _json_objects(text: str) -> Iterator[dict[str, Any]]:
    """Yield every top-level JSON object embedded in captured output."""
    decoder = json.JSONDecoder()
    index = text.find("{")
    while index != -1:
        try:
            payload, _end = decoder.raw_decode(text[index:])
        except ValueError:
            index = text.find("{", index + 1)
            continue
        if isinstance(payload, dict):
            yield payload
        index = text.find("{", index + 1)


def envelope_error(text: str) -> dict[str, Any]:
    """Return the review error envelope in *text*, or an empty dict.

    The same rule as the classifier's ``_parse_error_envelope``: the last
    JSON object whose ``error`` names a string ``kind``, because a logged
    provider body (``{"type":"error","error":{...}}``) can carry an ``error``
    object of its own ahead of lintro's envelope, and lintro's always names
    a kind. When no object names one, the first ``error`` object stands in.
    The two scripts read the same attempt output and must agree on which
    object is the attempt's, or a walk could be recorded that the verdict
    does not see.

    Args:
        text: The attempt's captured stdout and stderr.

    Returns:
        The ``error`` object of the attempt's envelope.
    """
    first: dict[str, Any] = {}
    with_kind: dict[str, Any] = {}
    for payload in _json_objects(text):
        error = payload.get("error")
        if not isinstance(error, dict):
            continue
        if not first:
            first = error
        if isinstance(error.get("kind"), str):
            with_kind = error
    return with_kind or first


def error_kind(text: str) -> str:
    """Return the kind of the attempt's review error envelope, or empty.

    Args:
        text: The attempt's captured stdout and stderr.

    Returns:
        The envelope's ``error.kind``, or an empty string when none is found.
    """
    kind = envelope_error(text).get("kind", "")
    return kind if isinstance(kind, str) else ""


def completed_chunks(error: dict[str, Any]) -> int | None:
    """Return the envelope's completed-chunk count, or ``None`` when absent.

    Args:
        error: The ``error`` object of an envelope.

    Returns:
        A non-negative integer, or ``None`` when the envelope carries no
        usable count (older lintro, a failure outside the run, or junk).
    """
    count = error.get("completed_chunks")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        return None
    return count


def chunk_completed(state_dir: Path, since: Path) -> bool:
    """Return whether a checkpoint part was written after *since*.

    Args:
        state_dir: The review state directory lintro checkpoints into.
        since: A marker file created when the attempt started.

    Returns:
        True when any ``part-*.json`` in *state_dir* is newer than the marker.
    """
    try:
        started = since.stat().st_mtime
    except OSError:
        # No marker means no way to tell; a chunk may have completed.
        return True
    if not state_dir.is_dir():
        return False
    # ``>=``: on a filesystem with one-second mtimes a part written in the
    # marker's second must still count; a downloaded prior part is minutes
    # older than the marker.
    return any(
        part.is_file() and part.stat().st_mtime >= started
        for part in state_dir.glob("part-*.json")
    )


def decide(
    *,
    status: int,
    output: str,
    state_dir: Path,
    since: Path,
    attempt: int,
    chain_length: int,
) -> tuple[str, str]:
    """Decide whether to advance to the next lane after an attempt.

    Args:
        status: ``lintro review``'s exit status for the attempt.
        output: The attempt's captured output.
        state_dir: The review state directory.
        since: The marker file created when the attempt started.
        attempt: The attempt's index into the chain.
        chain_length: The number of lanes in the chain.

    Returns:
        ``(ADVANCE, kind)`` to walk to the next lane, ``(EXHAUSTED, kind)``
        when the lane died before any chunk but none is left, or
        ``(STOP, reason)`` for every other outcome.
    """
    if status != REVIEW_STATUS_ERROR:
        return STOP, f"review exited {status}, not the error status"
    error = envelope_error(output)
    kind = error.get("kind", "")
    kind = kind if isinstance(kind, str) else ""
    if kind not in FALLBACK_KINDS:
        return STOP, f"error kind {kind or '(none)'!r} is not a fallback trigger"
    completed = completed_chunks(error)
    if completed is None:
        return (
            STOP,
            f"{kind} with no completed-chunk count on the envelope; "
            "the round stays on this lane",
        )
    if completed > 0 or chunk_completed(state_dir, since):
        return (
            STOP,
            f"{kind} after a chunk completed; the partial round stays on this lane",
        )
    if attempt + 1 >= chain_length:
        return EXHAUSTED, kind
    return ADVANCE, kind


def lanes_down(current: str, *, lane: str, kind: str) -> str:
    """Append a dead lane to the JSON list the wrapper carries.

    Args:
        current: The current JSON list (``[]`` when none).
        lane: The lane that failed before any chunk completed.
        kind: The error kind it failed with.

    Returns:
        The updated list as compact JSON.

    Raises:
        ValueError: When the lane or kind is not a plain identifier, or the
            current value is not a JSON list.
    """
    if not _LANE_ID_RE.fullmatch(lane):
        msg = f"lane {lane!r} is not a lane id"
        raise ValueError(msg)
    if not _KIND_RE.fullmatch(kind):
        msg = f"kind {kind!r} is not an error kind"
        raise ValueError(msg)
    try:
        existing = json.loads(current or "[]")
    except ValueError as exc:
        msg = f"current lanes-down value is not JSON: {exc}"
        raise ValueError(msg) from exc
    if not isinstance(existing, list):
        msg = "current lanes-down value is not a list"
        raise ValueError(msg)
    entries = [entry for entry in existing if isinstance(entry, dict)]
    entries.append({"lane": lane, "kind": kind})
    return json.dumps(entries, separators=(",", ":"))


def main(argv: list[str] | None = None) -> int:
    """Run one subcommand.

    Args:
        argv: Arguments, or ``None`` for ``sys.argv``.

    Returns:
        0 on success, 1 on a usage or value error (the wrapper then stops
        the walk rather than guessing).
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    decide_cmd = commands.add_parser("decide", help="advance or stop after an attempt")
    decide_cmd.add_argument("--status", type=int, required=True)
    decide_cmd.add_argument("--output-file", type=Path, required=True)
    decide_cmd.add_argument("--state-dir", type=Path, required=True)
    decide_cmd.add_argument("--since", type=Path, required=True)
    decide_cmd.add_argument("--attempt", type=int, required=True)
    decide_cmd.add_argument("--chain-length", type=int, required=True)
    down_cmd = commands.add_parser("lanes-down", help="append a dead lane to the list")
    down_cmd.add_argument("--current", default="[]")
    down_cmd.add_argument("--lane", required=True)
    down_cmd.add_argument("--kind", required=True)
    args = parser.parse_args(argv)
    if args.command == "decide":
        try:
            output = args.output_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            output = ""
        verb, detail = decide(
            status=args.status,
            output=output,
            state_dir=args.state_dir,
            since=args.since,
            attempt=args.attempt,
            chain_length=args.chain_length,
        )
        sys.stdout.write(f"{verb} {detail}\n")
        return 0
    try:
        sys.stdout.write(
            lanes_down(args.current, lane=args.lane, kind=args.kind) + "\n",
        )
    except ValueError as exc:
        sys.stderr.write(f"::error::review chain: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
