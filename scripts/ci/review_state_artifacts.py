#!/usr/bin/env python3
"""Locate and upload AI-review state artifacts (#2158 / #2156).

Cross-run download is not ``download-artifact``'s default. This helper lists
completed trusted runs of ``ai-review.yml`` and prints the newest run that
carries a valid ``lintro-review-state-pr-<N>-*`` artifact. The current run is
excluded. Conclusion is irrelevant: an INCOMPLETE (red) round is exactly the
run to resume from (#2154).

``upload`` writes the current ``ai-review-state/`` directory through the
Actions artifact service from inside ``run-ai-review.sh``. A cancelled job
skips later ``if: always()`` steps (dogfood #2166 round 5); an in-step
upload still attaches the persist snapshot so the next run can resume.

Missing, expired, unlistable, or malformed state degrades to empty — a full
re-review — and never fails the job. Lintro writes versioned parts under
``ai-review-state/`` (#2154).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import shutil
import subprocess  # nosec B404 - gh argv is built internally; shell=False
import sys
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlparse

WORKFLOW_FILENAME: Final[str] = "ai-review.yml"
WORKFLOW_PATH: Final[str] = f".github/workflows/{WORKFLOW_FILENAME}"
WORKFLOW_EVENT: Final[str] = "pull_request_target"
STATE_ARTIFACT_PREFIX: Final[str] = "lintro-review-state-pr-"
_STATE_NAME_RE: Final[re.Pattern[str]] = re.compile(
    rf"^{re.escape(STATE_ARTIFACT_PREFIX)}(\d+)-",
)
_GH_API_TIMEOUT_SECONDS: Final[int] = 30
_UPLOAD_TIMEOUT_SECONDS: Final[float] = 8.0
# Whole Create/PUT/Finalize after wait returns 143. GitHub's cancel
# grace is ~7.5s; the log flush already used some of it. A 8s×3 upload
# is SIGKILL'd and classify never runs (#2173).
_CANCEL_UPLOAD_BUDGET_SECONDS: Final[float] = 2.0
# Mid-run checkpoints happen while the job is healthy.
_CHECKPOINT_UPLOAD_BUDGET_SECONDS: Final[float] = 24.0
RUNS_PER_PAGE: Final[int] = 100
ARTIFACTS_PER_PAGE: Final[int] = 100
RETENTION_DAYS: Final[int] = 30
DEFAULT_STATE_DIR: Final[str] = "ai-review-state"
TWIRP_SERVICE: Final[str] = "github.actions.results.api.v1.ArtifactService"
RESULTS_HOST_SUFFIX: Final[str] = ".actions.githubusercontent.com"


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
HttpDo = Callable[[str, str, Mapping[str, str], bytes], tuple[int, bytes]]


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


def state_files(directory: Path) -> list[Path]:
    """Return JSON state files that are safe to merge across uploads.

    Prefer ``part-*.json`` so ``download-artifact`` ``merge-multiple``
    cannot overwrite ``state.json`` with an older checkpoint. Fall back
    to ``state.json`` only when this run has not written a part yet.

    Args:
        directory: Review-state directory.

    Returns:
        Sorted JSON paths. Missing directories yield empty.
    """
    if not directory.is_dir():
        return []
    parts = sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix == ".json" and path.name.startswith("part-")
    )
    if parts:
        return parts
    snapshot = directory / "state.json"
    return [snapshot] if snapshot.is_file() else []


def sanitize_artifact_suffix(raw: str) -> str:
    """Return a filename-safe artifact-name suffix.

    Args:
        raw: Caller-supplied suffix (``inline``, ``ckpt-15``, ...).

    Returns:
        Sanitized suffix; empty input becomes ``inline``.
    """
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", (raw or "").strip())
    cleaned = cleaned.replace("..", "-")
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-.")
    return cleaned or "inline"


def state_artifact_name(*, pr_number: int, attempt: int, suffix: str) -> str:
    """Return the artifact name for one in-step upload.

    Args:
        pr_number: Pull request number.
        attempt: ``GITHUB_RUN_ATTEMPT``.
        suffix: Distinguisher so v4 can upload more than once per run.

    Returns:
        Name matching ``lintro-review-state-pr-<N>-*``.
    """
    return (
        f"{STATE_ARTIFACT_PREFIX}{pr_number}-attempt-{attempt}-"
        f"{sanitize_artifact_suffix(suffix)}"
    )


def zip_state_files(files: Sequence[Path]) -> bytes:
    """Zip state files at the archive root.

    Args:
        files: JSON files to include.

    Returns:
        Zip bytes.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, arcname=path.name)
    return buffer.getvalue()


def backend_ids_from_token(token: str) -> tuple[str, str] | None:
    """Read Actions Results backend IDs from the runtime JWT.

    Args:
        token: ``ACTIONS_RUNTIME_TOKEN``.

    Returns:
        ``(workflow_run_backend_id, workflow_job_run_backend_id)``, or
        ``None`` when the token is not an Actions Results JWT.
    """
    parts = token.split(".")
    if len(parts) < 2:
        return None
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(payload, Mapping):
        return None
    scp = str(payload.get("scp", ""))
    for scope in scp.split():
        bits = scope.split(":")
        if len(bits) == 3 and bits[0] == "Actions.Results":
            return bits[1], bits[2]
    return None


def _results_origin(results_url: str) -> str | None:
    """Return the origin of ``ACTIONS_RESULTS_URL`` when it is trusted.

    Args:
        results_url: Raw results-service URL.

    Returns:
        ``scheme://host`` origin, or ``None`` when the host is not an
        Actions results endpoint.
    """
    parsed = urlparse(results_url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host.endswith(RESULTS_HOST_SUFFIX):
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _http_do(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    *,
    timeout: float = _UPLOAD_TIMEOUT_SECONDS,
) -> tuple[int, bytes]:
    """Issue one HTTPS request.

    Args:
        method: HTTP method.
        url: Absolute HTTPS URL.
        headers: Request headers.
        body: Request body; empty for no payload.
        timeout: Connect+read deadline for this request.

    Returns:
        Status code and response body. HTTP errors are returned, not raised.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return 0, b"refusing non-https artifact URL"
    request = urllib.request.Request(  # noqa: S310 - scheme checked above
        url,
        data=body or None,
        method=method,
        headers=dict(headers),
    )
    try:
        with urllib.request.urlopen(  # nosec B310 - https-only; URL from Actions
            request,
            timeout=max(0.05, timeout),
        ) as response:
            return int(response.status), response.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read()
    except (OSError, urllib.error.URLError):
        return 0, b"artifact request failed"


def _twirp_json(
    *,
    origin: str,
    method: str,
    token: str,
    payload: Mapping[str, Any],
    http_do: HttpDo,
) -> dict[str, Any] | None:
    """Call one ArtifactService Twirp method.

    Args:
        origin: Results-service origin.
        method: Twirp method name.
        token: Runtime token.
        payload: JSON body (proto field names).
        http_do: Injectable HTTP client.

    Returns:
        Decoded object, or ``None`` on failure.
    """
    url = f"{origin}/twirp/{TWIRP_SERVICE}/{method}"
    status, body = http_do(
        "POST",
        url,
        {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json.dumps(payload).encode("utf-8"),
    )
    if status < 200 or status >= 300:
        return None
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _bounded_http_do(
    http_do: HttpDo,
    *,
    budget_seconds: float,
) -> HttpDo:
    """Stop starting requests once the whole-upload budget is gone.

    Args:
        http_do: Underlying client.
        budget_seconds: Wall-clock budget for Create + PUT + Finalize.

    Returns:
        Client that returns ``(0, ...)`` when the deadline has passed.
    """
    deadline = time.monotonic() + max(0.0, budget_seconds)

    def bounded(
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
    ) -> tuple[int, bytes]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return 0, b"artifact upload budget exhausted"
        if http_do is _http_do:
            return _http_do(
                method,
                url,
                headers,
                body,
                timeout=min(_UPLOAD_TIMEOUT_SECONDS, remaining),
            )
        return http_do(method, url, headers, body)

    return bounded


def upload_state(
    *,
    directory: Path,
    name: str,
    token: str,
    results_url: str,
    http_do: HttpDo = _http_do,
    budget_seconds: float = _CHECKPOINT_UPLOAD_BUDGET_SECONDS,
) -> bool:
    """Upload JSON state files as one Actions artifact.

    Args:
        directory: Directory holding ``state.json`` / ``part-*.json``.
        name: Artifact name.
        token: ``ACTIONS_RUNTIME_TOKEN``.
        results_url: ``ACTIONS_RESULTS_URL``.
        http_do: Injectable HTTP client.
        budget_seconds: Wall-clock cap for the whole Create/PUT/Finalize
            walk. Cancel-path callers pass ``_CANCEL_UPLOAD_BUDGET_SECONDS``.

    Returns:
        True when Create + PUT + Finalize all succeeded.
    """
    files = state_files(directory)
    origin = _results_origin(results_url)
    ids = backend_ids_from_token(token)
    if not files or origin is None or ids is None:
        return False
    archive = zip_state_files(files)
    digest = hashlib.sha256(archive).hexdigest()
    client = _bounded_http_do(http_do, budget_seconds=budget_seconds)
    created = _twirp_json(
        origin=origin,
        method="CreateArtifact",
        token=token,
        payload={
            "workflowRunBackendId": ids[0],
            "workflowJobRunBackendId": ids[1],
            "name": name,
            "version": 4,
            "mimeType": "application/zip",
        },
        http_do=client,
    )
    if created is None or created.get("ok") is not True:
        return False
    signed = created.get("signedUploadUrl") or created.get("signed_upload_url")
    if not isinstance(signed, str) or not signed:
        return False
    put_status, _put_body = client(
        "PUT",
        signed,
        {
            "Content-Type": "application/zip",
            "x-ms-blob-type": "BlockBlob",
            "x-ms-blob-content-type": "application/zip",
            "x-ms-version": "2023-11-03",
        },
        archive,
    )
    if put_status < 200 or put_status >= 300:
        return False
    finalized = _twirp_json(
        origin=origin,
        method="FinalizeArtifact",
        token=token,
        payload={
            "workflowRunBackendId": ids[0],
            "workflowJobRunBackendId": ids[1],
            "name": name,
            "size": str(len(archive)),
            "hash": f"sha256:{digest}",
        },
        http_do=client,
    )
    return bool(finalized is not None and finalized.get("ok") is True)


def upload_from_env(
    env: Mapping[str, str],
    *,
    suffix: str,
    http_do: HttpDo = _http_do,
    budget_seconds: float | None = None,
) -> bool:
    """Upload current review state using process environment.

    Missing Actions context or a transport failure is a no-op.

    Args:
        env: Process environment.
        suffix: Artifact-name suffix (``inline``, ``ckpt-15``, ...).
        http_do: Injectable HTTP client.
        budget_seconds: Optional whole-upload cap. ``inline`` defaults to
            the cancel-path budget; checkpoints use the longer default.

    Returns:
        True when an artifact was uploaded.
    """
    token = env.get("ACTIONS_RUNTIME_TOKEN", "").strip()
    results_url = env.get("ACTIONS_RESULTS_URL", "").strip()
    pr_number = _parse_optional_int(env.get("PR_NUMBER"))
    if not token or not results_url or pr_number is None or pr_number <= 0:
        return False
    attempt = _parse_optional_int(env.get("GITHUB_RUN_ATTEMPT")) or 1
    directory = Path(env.get("LINTRO_REVIEW_STATE_DIR") or DEFAULT_STATE_DIR)
    if budget_seconds is None:
        budget_seconds = (
            _CANCEL_UPLOAD_BUDGET_SECONDS
            if suffix == "inline"
            else _CHECKPOINT_UPLOAD_BUDGET_SECONDS
        )
    try:
        return upload_state(
            directory=directory,
            name=state_artifact_name(
                pr_number=pr_number,
                attempt=attempt,
                suffix=suffix,
            ),
            token=token,
            results_url=results_url,
            http_do=http_do,
            budget_seconds=budget_seconds,
        )
    except Exception:  # noqa: BLE001 - fail-safe; never redden the review
        return False


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser.

    Returns:
        Parser for the ``locate`` and ``upload`` subcommands.
    """
    parser = argparse.ArgumentParser(
        description="Locate or upload an AI-review state artifact.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "locate",
        help="Write run-id= for the latest eligible prior-state run.",
    )
    upload = subparsers.add_parser(
        "upload",
        help="Upload ai-review-state/ from inside the review step.",
    )
    upload.add_argument(
        "--suffix",
        default="inline",
        help="Artifact-name suffix so a run can upload more than once.",
    )
    upload.add_argument(
        "--budget-seconds",
        type=float,
        default=None,
        help=(
            "Wall-clock cap for Create/PUT/Finalize. inline defaults to "
            f"{_CANCEL_UPLOAD_BUDGET_SECONDS:g}s so classify still runs "
            "inside SIGTERM grace."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Optional argument vector; ``None`` uses ``sys.argv``.

    Returns:
        Always ``0``. Empty ``run-id=`` / failed upload is the fail-safe.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "upload":
        uploaded = upload_from_env(
            os.environ,
            suffix=str(args.suffix),
            budget_seconds=args.budget_seconds,
        )
        if uploaded:
            sys.stdout.write(
                f"uploaded review-state artifact ({args.suffix})\n",
            )
        elif os.environ.get("GITHUB_ACTIONS") == "true":
            sys.stderr.write(
                f"review-state upload skipped or failed ({args.suffix})\n",
            )
        return 0
    output_raw = os.environ.get("GITHUB_OUTPUT", "").strip()
    output_path = Path(output_raw) if output_raw else None
    write_run_id(locate_from_env(os.environ), output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
