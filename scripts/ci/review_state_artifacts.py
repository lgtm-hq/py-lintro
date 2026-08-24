#!/usr/bin/env python3
"""Locate prior AI-review state artifacts across workflow runs (#2158).

Cross-run download is not ``download-artifact``'s default. This helper lists
completed trusted runs of ``ai-review.yml`` and prints the newest run that
carries a valid ``lintro-review-state-pr-<N>-*`` artifact. The current run is
excluded. Conclusion is irrelevant: an INCOMPLETE (red) round is exactly the
run to resume from (#2154).

Missing, expired, unlistable, or malformed state degrades to empty — a full
re-review — and never fails the job. Lintro does not write state yet; this
plumbing is inert until #2154.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess  # nosec B404 - gh argv is built internally; shell=False
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

WORKFLOW_FILENAME: Final[str] = "ai-review.yml"
WORKFLOW_PATH: Final[str] = f".github/workflows/{WORKFLOW_FILENAME}"
WORKFLOW_EVENT: Final[str] = "pull_request_target"
STATE_ARTIFACT_PREFIX: Final[str] = "lintro-review-state-pr-"
_STATE_NAME_RE: Final[re.Pattern[str]] = re.compile(
    rf"^{re.escape(STATE_ARTIFACT_PREFIX)}(\d+)-",
)
_GH_API_TIMEOUT_SECONDS: Final[int] = 30


@dataclass(frozen=True)
class Artifact:
    """One workflow artifact on a completed run.

    Attributes:
        name: Artifact name as uploaded.
        expired: Whether GitHub has already expired the payload.
    """

    name: str
    expired: bool = False


@dataclass(frozen=True)
class WorkflowRun:
    """A completed Actions run of the AI-review workflow.

    Attributes:
        run_id: Actions run id.
        event: Trigger event (must be ``pull_request_target``).
        status: Run status (must be ``completed``).
        path: Workflow path recorded on the run.
        created_at: When the run was created; newest wins.
    """

    run_id: int
    event: str
    status: str
    path: str
    created_at: datetime


GhApi = Callable[[str], Any | None]


def state_artifact_prefix(pr_number: int) -> str:
    """Return the exact artifact-name prefix for a pull request.

    Args:
        pr_number: Pull request number.

    Returns:
        Prefix including the trailing dash so PR 1 cannot match PR 12.
    """
    return f"{STATE_ARTIFACT_PREFIX}{pr_number}-"


def is_state_artifact_for_pr(name: str, pr_number: int) -> bool:
    """Return whether ``name`` is a state artifact for ``pr_number``.

    The PR number is parsed from the name rather than tested with
    ``str.startswith`` so ``lintro-review-state-pr-1-`` cannot match a
    PR 12 artifact.

    Args:
        name: Artifact name.
        pr_number: Pull request that owns the artifact.

    Returns:
        True when the name is a state artifact for that PR.
    """
    match = _STATE_NAME_RE.match(name)
    return match is not None and int(match.group(1)) == pr_number


def has_valid_state_artifact(
    artifacts: Sequence[Artifact],
    *,
    pr_number: int,
) -> bool:
    """Return whether any non-expired artifact is state for ``pr_number``.

    Args:
        artifacts: Artifacts attached to one run.
        pr_number: Pull request that must own the artifact.

    Returns:
        True when the run is eligible as a resume source.
    """
    return any(
        not artifact.expired and is_state_artifact_for_pr(artifact.name, pr_number)
        for artifact in artifacts
    )


def is_trusted_completed_run(run: WorkflowRun) -> bool:
    """Return whether ``run`` is a completed trusted AI-review run.

    Args:
        run: Candidate workflow run.

    Returns:
        True when event, status, and workflow path all match.
    """
    path_name = Path(run.path).name
    return (
        run.status == "completed"
        and run.event == WORKFLOW_EVENT
        and path_name == WORKFLOW_FILENAME
    )


def select_prior_run_id(
    runs: Sequence[WorkflowRun],
    artifacts_by_run: Mapping[int, Sequence[Artifact]],
    *,
    pr_number: int,
    current_run_id: int | None,
) -> int | None:
    """Select the latest completed trusted run that carries valid state.

    Conclusion is not consulted. The current run is never selected.

    Args:
        runs: Candidate runs; order does not matter.
        artifacts_by_run: Artifacts keyed by run id.
        pr_number: Pull request whose state is being resumed.
        current_run_id: Run to exclude, if known.

    Returns:
        The selected run id, or ``None`` when nothing is eligible.
    """
    eligible = [
        run
        for run in runs
        if is_trusted_completed_run(run)
        and run.run_id != current_run_id
        and has_valid_state_artifact(
            artifacts_by_run.get(run.run_id, ()),
            pr_number=pr_number,
        )
    ]
    if not eligible:
        return None
    newest = max(eligible, key=lambda run: (run.created_at, run.run_id))
    return newest.run_id


def _parse_datetime(raw: str) -> datetime:
    """Parse a GitHub timestamp, accepting the trailing ``Z``.

    Args:
        raw: ISO-8601 timestamp from the Actions API.

    Returns:
        Timezone-aware datetime. Unix epoch when ``raw`` is empty or invalid.
    """
    text = raw.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return datetime.fromtimestamp(0).astimezone()


def parse_workflow_run(payload: Mapping[str, Any]) -> WorkflowRun | None:
    """Parse one Actions workflow-run object.

    Args:
        payload: Raw run mapping from the Actions API.

    Returns:
        The run, or ``None`` when required fields are missing.
    """
    try:
        run_id = int(payload["id"])
    except (KeyError, TypeError, ValueError):
        return None
    return WorkflowRun(
        run_id=run_id,
        event=str(payload.get("event", "")),
        status=str(payload.get("status", "")),
        path=str(payload.get("path", "")),
        created_at=_parse_datetime(str(payload.get("created_at", ""))),
    )


def parse_artifact(payload: Mapping[str, Any]) -> Artifact | None:
    """Parse one Actions artifact object.

    Args:
        payload: Raw artifact mapping from the Actions API.

    Returns:
        The artifact, or ``None`` when the name is missing.
    """
    name = payload.get("name")
    if not isinstance(name, str) or not name:
        return None
    return Artifact(name=name, expired=bool(payload.get("expired", False)))


def _gh_api(path: str) -> Any | None:
    """Call ``gh api`` and return the decoded JSON body.

    Args:
        path: REST path passed to ``gh api``.

    Returns:
        Decoded JSON, or ``None`` on any failure.
    """
    gh_bin = shutil.which("gh")
    if gh_bin is None:
        return None
    try:
        result = subprocess.run(  # nosec B603 - resolved gh path; shell=False
            [gh_bin, "api", path],
            capture_output=True,
            text=True,
            timeout=_GH_API_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _as_object_list(payload: Any, key: str) -> list[Mapping[str, Any]]:
    """Return ``payload[key]`` when it is a list of mappings.

    Args:
        payload: Decoded JSON body.
        key: Array field name.

    Returns:
        The list, or empty when the shape is wrong.
    """
    if not isinstance(payload, Mapping):
        return []
    items = payload.get(key)
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, Mapping)]


def fetch_completed_runs(repo: str, *, gh_api: GhApi = _gh_api) -> list[WorkflowRun]:
    """List completed ``ai-review.yml`` runs for ``repo``.

    Args:
        repo: ``owner/name`` repository slug.
        gh_api: Injectable GitHub API caller.

    Returns:
        Parsed runs; unparseable entries are dropped.
    """
    path = (
        f"repos/{repo}/actions/workflows/{WORKFLOW_FILENAME}/runs"
        f"?event={WORKFLOW_EVENT}&status=completed&per_page=50"
    )
    payload = gh_api(path)
    runs: list[WorkflowRun] = []
    for item in _as_object_list(payload, "workflow_runs"):
        parsed = parse_workflow_run(item)
        if parsed is not None:
            runs.append(parsed)
    return runs


def fetch_artifacts(
    repo: str,
    run_id: int,
    *,
    gh_api: GhApi = _gh_api,
) -> list[Artifact]:
    """List artifacts on one workflow run.

    Args:
        repo: ``owner/name`` repository slug.
        run_id: Actions run id.
        gh_api: Injectable GitHub API caller.

    Returns:
        Parsed artifacts; unparseable entries are dropped.
    """
    payload = gh_api(f"repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100")
    artifacts: list[Artifact] = []
    for item in _as_object_list(payload, "artifacts"):
        parsed = parse_artifact(item)
        if parsed is not None:
            artifacts.append(parsed)
    return artifacts


def locate_prior_run_id(
    *,
    repo: str,
    pr_number: int,
    current_run_id: int | None,
    gh_api: GhApi = _gh_api,
) -> int | None:
    """Fetch runs and select the latest eligible prior-state run.

    Args:
        repo: ``owner/name`` repository slug.
        pr_number: Pull request whose state is being resumed.
        current_run_id: Run to exclude, if known.
        gh_api: Injectable GitHub API caller.

    Returns:
        The selected run id, or ``None`` when nothing is eligible.
    """
    runs = fetch_completed_runs(repo, gh_api=gh_api)
    artifacts_by_run = {
        run.run_id: fetch_artifacts(repo, run.run_id, gh_api=gh_api) for run in runs
    }
    return select_prior_run_id(
        runs,
        artifacts_by_run,
        pr_number=pr_number,
        current_run_id=current_run_id,
    )


def write_run_id(run_id: int | None, output_path: Path | None) -> None:
    """Write ``run-id=`` for ``actions/download-artifact``.

    Args:
        run_id: Selected run, or ``None`` when the caller should no-op.
        output_path: ``GITHUB_OUTPUT`` path, or ``None`` to write stdout.
    """
    line = f"run-id={'' if run_id is None else run_id}\n"
    if output_path is None:
        sys.stdout.write(line)
        return
    with output_path.open("a", encoding="utf-8") as handle:
        handle.write(line)


def _parse_optional_int(raw: str | None) -> int | None:
    """Parse a decimal integer, treating blank as missing.

    Args:
        raw: Raw environment value.

    Returns:
        The integer, or ``None`` when unset or not a decimal.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def locate_from_env(
    env: Mapping[str, str],
    *,
    gh_api: GhApi = _gh_api,
) -> int | None:
    """Resolve the prior-state run from process environment.

    Any missing required value or API failure becomes ``None`` (fail-safe).

    Args:
        env: Process environment.
        gh_api: Injectable GitHub API caller.

    Returns:
        The selected run id, or ``None``.
    """
    repo = env.get("GITHUB_REPOSITORY", "").strip()
    pr_number = _parse_optional_int(env.get("PR_NUMBER"))
    if not repo or pr_number is None or pr_number <= 0:
        return None
    current_run_id = _parse_optional_int(env.get("GITHUB_RUN_ID"))
    try:
        return locate_prior_run_id(
            repo=repo,
            pr_number=pr_number,
            current_run_id=current_run_id,
            gh_api=gh_api,
        )
    except Exception:  # noqa: BLE001 - fail-safe empty state, never fail the job
        return None


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser.

    Returns:
        Parser for the ``locate`` subcommand.
    """
    parser = argparse.ArgumentParser(
        description="Locate a prior AI-review state artifact run.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "locate",
        help="Write run-id= for the latest eligible prior-state run.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Optional argument vector; ``None`` uses ``sys.argv``.

    Returns:
        Always ``0``. Empty ``run-id=`` is the no-op / fail-safe result.
    """
    parser = build_parser()
    parser.parse_args(argv)
    output_raw = os.environ.get("GITHUB_OUTPUT", "").strip()
    output_path = Path(output_raw) if output_raw else None
    write_run_id(locate_from_env(os.environ), output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
