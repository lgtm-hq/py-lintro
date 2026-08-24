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
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
RUNS_PER_PAGE: Final[int] = 100
ARTIFACTS_PER_PAGE: Final[int] = 100
RETENTION_DAYS: Final[int] = 30


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
        pull_request_numbers: PRs attached on the run. Empty on some
            ``pull_request_target`` payloads; then artifact names decide.
    """

    run_id: int
    event: str
    status: str
    path: str
    created_at: datetime
    pull_request_numbers: tuple[int, ...] = ()


GhApi = Callable[[str], Any | None]
ApiObject = Mapping[str, Any]


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


def is_within_retention(run: WorkflowRun, *, now: datetime) -> bool:
    """Return whether ``run`` can still hold a non-expired state artifact.

    Args:
        run: Candidate workflow run.
        now: Clock used for the retention cutoff.

    Returns:
        True when ``created_at`` is inside ``RETENTION_DAYS``.
    """
    created = run.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    current = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    return created >= current - timedelta(days=RETENTION_DAYS)


def mentions_other_pr(run: WorkflowRun, *, pr_number: int) -> bool:
    """Return whether the run is known to belong to a different PR.

    An empty ``pull_requests`` list is treated as unknown — common for
    ``pull_request_target`` — so the caller still checks artifact names.

    Args:
        run: Candidate workflow run.
        pr_number: Pull request being resumed.

    Returns:
        True when the run lists PRs and this PR is not among them.
    """
    return bool(run.pull_request_numbers) and pr_number not in run.pull_request_numbers


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
    now: datetime,
) -> int | None:
    """Select the latest completed trusted run that carries valid state.

    Conclusion is not consulted. The current run is never selected.
    Runs older than the artifact retention window cannot be eligible.

    Args:
        runs: Candidate runs; order does not matter.
        artifacts_by_run: Artifacts keyed by run id.
        pr_number: Pull request whose state is being resumed.
        current_run_id: Run to exclude, if known.
        now: Clock used for the retention cutoff.

    Returns:
        The selected run id, or ``None`` when nothing is eligible.
    """
    eligible = [
        run
        for run in runs
        if is_trusted_completed_run(run)
        and run.run_id != current_run_id
        and is_within_retention(run, now=now)
        and not mentions_other_pr(run, pr_number=pr_number)
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
        return datetime.fromtimestamp(0, tz=UTC)


def _parse_pr_numbers(payload: Mapping[str, Any]) -> tuple[int, ...]:
    """Extract pull-request numbers from a workflow-run payload.

    Args:
        payload: Raw run mapping from the Actions API.

    Returns:
        Parsed PR numbers, possibly empty.
    """
    raw = payload.get("pull_requests")
    if not isinstance(raw, list):
        return ()
    numbers: list[int] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        try:
            numbers.append(int(item["number"]))
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(numbers)


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
        pull_request_numbers=_parse_pr_numbers(payload),
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


def _yield_api_pages(
    path: str,
    key: str,
    *,
    per_page: int,
    gh_api: GhApi,
) -> Iterator[ApiObject]:
    """Yield objects from a GitHub list endpoint, page by page.

    Stops on an API failure or a short page so callers can return at the
    first match without fetching the rest of the history.

    Args:
        path: REST path including any non-paging query string.
        key: Array field name on each page body.
        per_page: Page size.
        gh_api: Injectable GitHub API caller.

    Yields:
        ApiObject: Object mappings in API order (newest first for
            workflow runs).
    """
    page = 1
    separator = "&" if "?" in path else "?"
    while True:
        payload = gh_api(f"{path}{separator}per_page={per_page}&page={page}")
        if payload is None:
            return
        page_items = _as_object_list(payload, key)
        if not page_items:
            return
        yield from page_items
        if len(page_items) < per_page:
            return
        page += 1


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
    artifacts: list[Artifact] = []
    for item in _yield_api_pages(
        f"repos/{repo}/actions/runs/{run_id}/artifacts",
        "artifacts",
        per_page=ARTIFACTS_PER_PAGE,
        gh_api=gh_api,
    ):
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
    now: datetime | None = None,
) -> int | None:
    """Walk completed runs newest-first and return the first eligible.

    Pages until a valid state artifact is found so a busy repo cannot
    hide resume state behind a fixed window. Stops at the first match
    because the Actions API returns ``created_at`` descending. Stops
    entirely once runs fall outside the 30-day artifact retention
    window — an older run cannot hold a non-expired artifact.

    Args:
        repo: ``owner/name`` repository slug.
        pr_number: Pull request whose state is being resumed.
        current_run_id: Run to exclude, if known.
        gh_api: Injectable GitHub API caller.
        now: Clock used for the retention cutoff; defaults to UTC now.

    Returns:
        The selected run id, or ``None`` when nothing is eligible.
    """
    clock = now if now is not None else datetime.now(tz=UTC)
    path = (
        f"repos/{repo}/actions/workflows/{WORKFLOW_FILENAME}/runs"
        f"?event={WORKFLOW_EVENT}&status=completed"
    )
    for item in _yield_api_pages(
        path,
        "workflow_runs",
        per_page=RUNS_PER_PAGE,
        gh_api=gh_api,
    ):
        run = parse_workflow_run(item)
        if run is None:
            continue
        if not is_trusted_completed_run(run) or run.run_id == current_run_id:
            continue
        if not is_within_retention(run, now=clock):
            return None
        if mentions_other_pr(run, pr_number=pr_number):
            continue
        artifacts = fetch_artifacts(repo, run.run_id, gh_api=gh_api)
        if has_valid_state_artifact(artifacts, pr_number=pr_number):
            return run.run_id
    return None


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
    now: datetime | None = None,
) -> int | None:
    """Resolve the prior-state run from process environment.

    Any missing required value or API failure becomes ``None`` (fail-safe).

    Args:
        env: Process environment.
        gh_api: Injectable GitHub API caller.
        now: Clock used for the retention cutoff; defaults to UTC now.

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
            now=now,
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
