#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Re-run a stale red All Checks Gate after a watched workflow recovers.

``auto-rerun-on-infra-failure.yml`` re-runs the failed jobs of a watched
workflow when they match an infra signature. The All Checks Gate on the same
commit has usually finished by then: it saw the first attempt fail and went
red. The retry then goes green, but nothing re-evaluates the gate, so ``main``
(or a PR) stays red until someone re-runs it by hand. On 2026-11-04 that left
``main`` red for an hour after CI - Docker attempt 2 passed (run 41776692371).

This script is the second half of that watcher. It runs on the
``workflow_run`` completion of a watched workflow and, when that completion is
a *later* attempt that concluded ``success``, re-runs every failed gate run
for the same head SHA that finished before the latest recovery on that SHA.

The cutoff is not the triggering event's own timestamp. The job is serialized
per SHA, and GitHub keeps one *pending* run per concurrency group, replacing
it by queue arrival rather than by when the producer recovered. A delayed
event for an early recovery can therefore displace the event for a later one.
So the script asks the API for every same-repo workflow run on the SHA, takes
the newest ``updated_at`` among the successful retries, and uses that as the
cutoff. Whichever job survives covers every recovery that finished before it
ran, and a recovery that finishes later sends its own event.

The gate is never given a retry budget of its own: it is re-run only when one
of its producers turned green, and the re-run gate re-judges every check from
scratch, so a genuinely red producer keeps it red. Re-judging works because
lgtm-ci's gate (``reusable-all-checks-gate.yml``, v0.79.2 and later, now on
the canonical pin) keeps only the latest attempt of each job's check run, so
the failed attempt 1 no longer counts. Re-running gate 41776692371 by hand did
exactly that: "All 131 other check(s) passed".

A ``pull_request`` gate is skipped once its PR has a newer head: the gate's
concurrency group is per PR with ``cancel-in-progress``, so re-running the old
head's gate would cancel the live one. A gate that fails in the same second as
the recovery is not considered stale (one-second timestamps) and needs a human.

Every gate is handled independently. A failed API call on one gate is
reported and the loop continues; the script exits 1 at the end if any gate
could not be refreshed. A rerun POST is never retried, because an ambiguous
error may already have been applied.

Environment:
    GH_TOKEN / GITHUB_TOKEN: token with ``actions: write``.
    GITHUB_REPOSITORY: ``owner/name`` slug.
    GITHUB_EVENT_PATH: the ``workflow_run`` event payload.

Exit codes:
    0 - Nothing to do, or every eligible gate was re-run.
    1 - The event was malformed or at least one gate could not be re-run.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from github_api import gh_json as _gh_json
except ModuleNotFoundError:
    from scripts.ci.github_api import gh_json as _gh_json

GATE_WORKFLOW_FILE = "all-checks-gate.yml"
GATE_PATH = f".github/workflows/{GATE_WORKFLOW_FILE}"
GATE_NAME = "PR - All Checks Gate"
RUNS_PER_PAGE = 100
#: Upper bound on pages read; a single SHA never carries this many gate runs.
MAX_PAGES = 10
_SHA = re.compile(r"^[0-9a-f]{40}$")

GhJson = Callable[..., Any]


def _timestamp(value: object) -> datetime:
    """Parse a GitHub ISO-8601 timestamp.

    Args:
        value: Timestamp string such as ``2026-11-04T05:47:00Z``.

    Returns:
        The timezone-aware datetime.

    Raises:
        ValueError: If ``value`` is not a timestamp string.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"not a timestamp: {value!r}")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def is_recovering_attempt(*, run: dict[str, Any]) -> bool:
    """Return whether ``run`` is a successful retry of a non-gate workflow.

    Args:
        run: A ``workflow_run`` object from the event or the runs API.

    Returns:
        True for a completed, successful attempt 2+ of any workflow other
        than the gate itself.
    """
    attempt = run.get("run_attempt")
    return (
        run.get("status") == "completed"
        and run.get("conclusion") == "success"
        and isinstance(attempt, int)
        and attempt > 1
        and run.get("path") != GATE_PATH
        and run.get("name") != GATE_NAME
    )


def is_stale_failed_gate(*, gate: dict[str, Any], head_sha: str, before: str) -> bool:
    """Return whether ``gate`` is a red gate the recovery has made stale.

    Args:
        gate: A workflow run object for a gate run.
        head_sha: Head SHA of the recovering workflow attempt.
        before: ``updated_at`` of the latest recovering run on the SHA; only
            gates that finished strictly earlier judged a failed attempt.

    Returns:
        True when the gate is a completed failure on the same SHA that
        finished before the recovering attempt.
    """
    try:
        finished = _timestamp(gate.get("updated_at"))
        recovered = _timestamp(before)
    except ValueError:
        return False
    return (
        gate.get("head_sha") == head_sha
        and gate.get("path") == GATE_PATH
        and gate.get("status") == "completed"
        and gate.get("conclusion") == "failure"
        and finished < recovered
    )


def pr_head_moved(*, repository: str, gate: dict[str, Any], gh: GhJson) -> bool:
    """Return whether a PR gate's pull request has moved past its head SHA.

    ``all-checks-gate.yml`` groups PR runs per PR number with
    ``cancel-in-progress``, so re-running an old head's gate would cancel the
    live gate for the PR's current head. Push gates group per SHA and merge
    queue gates per ref, so only ``pull_request`` runs need this check.

    Args:
        repository: ``owner/name`` slug.
        gate: A gate workflow run object.
        gh: ``gh api`` JSON caller.

    Returns:
        True when any PR attached to the run now points at a different head,
        or when a ``pull_request`` run names no PR to check against.
    """
    if gate.get("event") != "pull_request":
        return False
    prs = gate.get("pull_requests") or []
    numbers = [pr.get("number") for pr in prs if isinstance(pr, dict)]
    if not numbers or not all(isinstance(n, int) for n in numbers):
        return True
    for number in numbers:
        pull = gh(f"repos/{repository}/pulls/{number}")
        if not isinstance(pull, dict):
            return True
        head = pull.get("head") or {}
        if pull.get("state") != "open" or head.get("sha") != gate.get("head_sha"):
            return True
    return False


def list_gate_runs(
    *,
    repository: str,
    head_sha: str,
    gh: GhJson,
) -> list[dict[str, Any]]:
    """List every gate run on ``head_sha``, across push/PR/merge-queue events.

    Args:
        repository: ``owner/name`` slug.
        head_sha: Commit SHA to filter on.
        gh: ``gh api`` JSON caller.

    Returns:
        Gate runs, deduplicated by id.
    """
    runs: dict[int, dict[str, Any]] = {}
    for page in range(1, MAX_PAGES + 1):
        payload = gh(
            f"repos/{repository}/actions/workflows/{GATE_WORKFLOW_FILE}/runs"
            f"?head_sha={head_sha}&per_page={RUNS_PER_PAGE}&page={page}",
        )
        batch = payload.get("workflow_runs", []) if isinstance(payload, dict) else []
        for run in batch:
            if isinstance(run, dict) and isinstance(run.get("id"), int):
                runs[run["id"]] = run
        if len(batch) < RUNS_PER_PAGE:
            break
    return list(runs.values())


def latest_recovery(
    *,
    repository: str,
    head_sha: str,
    gh: GhJson,
) -> str | None:
    """Return when the newest successful retry on ``head_sha`` finished.

    The runs API reports each run's latest attempt, so a retry that was
    re-run again and failed no longer counts, and a replayed event cannot
    bring it back. Runs from another repository on the same SHA (fork PRs)
    are ignored.

    Args:
        repository: ``owner/name`` slug.
        head_sha: Commit SHA to filter on.
        gh: ``gh api`` JSON caller.

    Returns:
        The ``updated_at`` of the newest recovering run, or None when the
        SHA has no current successful retry.
    """
    newest: tuple[datetime, str] | None = None
    for page in range(1, MAX_PAGES + 1):
        payload = gh(
            f"repos/{repository}/actions/runs"
            f"?head_sha={head_sha}&per_page={RUNS_PER_PAGE}&page={page}",
        )
        batch = payload.get("workflow_runs", []) if isinstance(payload, dict) else []
        for run in batch:
            if not isinstance(run, dict) or run.get("head_sha") != head_sha:
                continue
            head_repo = run.get("head_repository") or {}
            if head_repo.get("full_name") != repository:
                continue
            if not is_recovering_attempt(run=run):
                continue
            try:
                finished = _timestamp(run.get("updated_at"))
            except ValueError:
                continue
            if newest is None or finished > newest[0]:
                newest = (finished, str(run["updated_at"]))
        if len(batch) < RUNS_PER_PAGE:
            break
    return newest[1] if newest else None


def refresh(*, repository: str, trigger: dict[str, Any], gh: GhJson) -> int:
    """Re-run every stale red gate for the trigger's head SHA.

    Args:
        repository: ``owner/name`` slug.
        trigger: The ``workflow_run`` object from the event payload.
        gh: ``gh api`` JSON caller.

    Returns:
        Process exit code.
    """
    if not is_recovering_attempt(run=trigger):
        print("Not a successful retry of a watched workflow; nothing to refresh")
        return 0
    head_sha = trigger.get("head_sha")
    if not isinstance(head_sha, str) or not _SHA.fullmatch(head_sha):
        print(f"::error::Malformed head_sha in event: {head_sha!r}", file=sys.stderr)
        return 1

    # The event only says that something on this SHA recovered. Pending
    # concurrency runs replace each other by arrival, so this may be an older
    # recovery than the one it displaced, or a replay of an attempt that was
    # retried again since. Take the cutoff from the API's current state.
    before = latest_recovery(repository=repository, head_sha=head_sha, gh=gh)
    if before is None:
        print(f"No current successful retry on {head_sha}; skipping")
        return 0

    failures = 0
    refreshed = 0
    for listed in list_gate_runs(repository=repository, head_sha=head_sha, gh=gh):
        gate_id = listed["id"]
        try:
            # Re-read right before writing rather than trusting the listing.
            gate = gh(f"repos/{repository}/actions/runs/{gate_id}")
            if not isinstance(gate, dict) or not is_stale_failed_gate(
                gate=gate,
                head_sha=head_sha,
                before=before,
            ):
                continue
            if pr_head_moved(repository=repository, gate=gate, gh=gh):
                print(f"Gate {gate_id}: its PR has a newer head; leaving it")
                continue
            gh(
                "--method",
                "POST",
                f"repos/{repository}/actions/runs/{gate_id}/rerun-failed-jobs",
            )
        except (RuntimeError, json.JSONDecodeError) as exc:
            failures += 1
            print(
                f"::error::Could not re-run gate {gate_id}: {exc}",
                file=sys.stderr,
            )
            continue
        refreshed += 1
        print(
            f"Re-ran gate {gate_id} (attempt {gate.get('run_attempt')}); the "
            f"latest recovery on {head_sha} finished at {before}",
        )
    if refreshed == 0 and failures == 0:
        print(f"No stale red gate on {head_sha}")
    return 1 if failures else 0


def main() -> int:
    """Read the event payload and refresh stale gates.

    Returns:
        Process exit code.
    """
    try:
        repository = os.environ["GITHUB_REPOSITORY"]
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text("utf-8"))
        trigger = event["workflow_run"]
    except (KeyError, OSError, json.JSONDecodeError, TypeError) as exc:
        print(f"::error::Unreadable workflow_run event: {exc}", file=sys.stderr)
        return 1
    if not isinstance(trigger, dict):
        print("::error::workflow_run is not an object", file=sys.stderr)
        return 1
    try:
        return refresh(repository=repository, trigger=trigger, gh=_gh_json)
    except (RuntimeError, json.JSONDecodeError) as exc:
        print(f"::error::GitHub API read failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
