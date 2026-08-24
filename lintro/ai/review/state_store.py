"""Artifact and local-ledger backends for review state (#2154).

CI writes versioned JSON parts under ``ai-review-state/`` (or
``LINTRO_REVIEW_STATE_DIR``). The consumer unions every valid part,
last-writer-wins per ``(path, hash)``. Local runs use
``.lintro-cache/ai/review-state/`` with atomic replace and a named LRU.
CI never imports a local ledger (and the reverse): trust boundary.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from lintro.ai.review.github_constants import STATE_VERSION_V1
from lintro.ai.review.models.coverage_record import CoverageRecord
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.flagged_file import FlaggedFile
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.review_state_codec import decode_state

__all__ = [
    "ARTIFACT_STATE_VERSION",
    "CI_STATE_DIRNAME",
    "LOCAL_STATE_DIR",
    "LOCAL_STATE_LRU",
    "load_ci_state",
    "load_local_state",
    "migrate_legacy_sticky",
    "state_dir",
    "union_states",
    "write_local_state",
    "write_state_part",
]

ARTIFACT_STATE_VERSION = 3
CI_STATE_DIRNAME = "ai-review-state"
LOCAL_STATE_DIR = Path(".lintro-cache/ai/review-state")
LOCAL_STATE_LRU = 32
_UNSAFE_KEY = re.compile(r"[^A-Za-z0-9._-]+")


def state_dir(*, ci: bool) -> Path:
    """Return the directory used for this invocation's state files.

    Args:
        ci: True when running under GitHub Actions with an artifact dir.

    Returns:
        Absolute or cwd-relative path. ``LINTRO_REVIEW_STATE_DIR`` wins
        when set so tests and the workflow can point at the download.
    """
    override = os.environ.get("LINTRO_REVIEW_STATE_DIR", "").strip()
    if override:
        return Path(override)
    if ci or os.environ.get("GITHUB_ACTIONS") == "true":
        return Path(CI_STATE_DIRNAME)
    return LOCAL_STATE_DIR


def write_state_part(
    *,
    state: ReviewState,
    directory: Path,
    sequence: int,
    final: bool = False,
) -> Path:
    """Atomically write one state part (or the final snapshot).

    Args:
        state: State to persist.
        directory: Destination directory (created if needed).
        sequence: Monotonic part number for this run.
        final: When True, also write ``state.json`` as the latest snapshot.

    Returns:
        Path of the part file.
    """
    directory.mkdir(parents=True, exist_ok=True)
    payload = state.to_artifact_dict()
    part = directory / f"part-{sequence:04d}.json"
    _atomic_write_json(path=part, payload=payload)
    if sequence == 1:
        # A new run (or the CLI's final persist) starts at part 0001.
        # Drop leftover downloaded parts so a stale ``stale.py`` record
        # cannot survive the last-writer-wins ``(path, hash)`` union.
        for leftover in directory.glob("part-*.json"):
            if leftover.resolve() != part.resolve():
                leftover.unlink(missing_ok=True)
    if final:
        _atomic_write_json(path=directory / "state.json", payload=payload)
    return part


def load_ci_state(
    *,
    directory: Path,
    repo: str,
    pr_number: int,
) -> ReviewState:
    """Load and union every valid part in *directory*.

    Invalid, wrong-repo, wrong-PR, or unknown-version files are skipped
    (fail toward empty coverage / more review).

    Args:
        directory: Downloaded artifact directory.
        repo: Expected ``owner/name``.
        pr_number: Expected pull request number.

    Returns:
        Unioned state, or empty state when nothing validates.
    """
    if not directory.is_dir():
        return ReviewState()
    parts = sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix == ".json"
        ),
        key=lambda path: path.name,
    )
    loaded: list[ReviewState] = []
    for path in parts:
        parsed = _load_artifact_file(
            path=path,
            repo=repo,
            pr_number=pr_number,
        )
        if parsed is not None:
            loaded.append(parsed)
    if not loaded:
        return ReviewState()
    return union_states(loaded)


def union_states(states: Iterable[ReviewState]) -> ReviewState:
    """Last-writer-wins union of coverage keyed by ``(path, hash)``.

    Args:
        states: Parts in ascending sequence order.

    Returns:
        Combined state. Findings and runs take the last non-empty copy
        so a later part that finished the round wins.
    """
    coverage: dict[tuple[str, str], CoverageRecord] = {}
    flagged: dict[tuple[str, str], FlaggedFile] = {}
    runs: tuple[RunRecord, ...] = ()
    findings: tuple[FindingRecord, ...] = ()
    pending: tuple[tuple[str, str], ...] = ()
    consumed: dict[tuple[str, str], None] = {}
    legacy = False
    truncated = False
    identity = ReviewState()
    for state in states:
        identity = state
        for record in state.coverage:
            coverage[record.identity] = record
        for flag in state.flagged_files:
            flagged[(flag.path, flag.patch_hash)] = flag
        if state.runs:
            runs = state.runs
        if state.findings:
            findings = state.findings
        pending = state.pending_invalidations
        for key in state.consumed_flags:
            consumed[key] = None
        legacy = legacy or state.legacy
        truncated = truncated or state.truncated
    return ReviewState(
        version=ARTIFACT_STATE_VERSION,
        runs=runs,
        findings=findings,
        coverage=tuple(coverage.values()),
        flagged_files=tuple(flagged.values()),
        pending_invalidations=pending,
        consumed_flags=tuple(consumed),
        repo=identity.repo,
        pr_number=identity.pr_number,
        base_sha=identity.base_sha,
        head_sha=identity.head_sha,
        workflow=identity.workflow,
        event=identity.event,
        run_id=identity.run_id,
        lintro_version=identity.lintro_version,
        legacy=legacy,
        truncated=truncated,
    )


def migrate_legacy_sticky(*, body: str) -> ReviewState:
    """Seed findings and runs from a v1/v2 sticky blob.

    Coverage is never seeded. Migrated history is marked ``legacy`` so
    rendering can label it and the gate cannot go greener from it.

    Args:
        body: Sticky comment body, possibly without a blob.

    Returns:
        State with findings/runs only, or empty state.
    """
    state = decode_state(body=body)
    if state.version <= STATE_VERSION_V1 and not state.runs and not state.findings:
        return ReviewState()
    return ReviewState(
        version=ARTIFACT_STATE_VERSION,
        runs=state.runs,
        findings=state.findings,
        coverage=(),
        flagged_files=(),
        legacy=True,
        truncated=state.truncated,
    )


def write_local_state(
    *,
    state: ReviewState,
    key: str,
    directory: Path | None = None,
) -> Path:
    """Atomically write a local ledger entry and enforce the LRU bound.

    Args:
        state: State to persist.
        key: PR number (``pr-123``) or sanitized branch name.
        directory: Ledger directory; defaults to :data:`LOCAL_STATE_DIR`.

    Returns:
        Path of the written file.
    """
    root = directory or LOCAL_STATE_DIR
    root.mkdir(parents=True, exist_ok=True)
    safe = _sanitize_key(key)
    path = root / f"{safe}.json"
    _atomic_write_json(path=path, payload=state.to_artifact_dict())
    _enforce_lru(root)
    return path


def load_local_state(
    *,
    key: str,
    directory: Path | None = None,
    repo: str = "",
    pr_number: int | None = None,
) -> ReviewState:
    """Load a local ledger entry.

    Args:
        key: PR number or sanitized branch name.
        directory: Ledger directory.
        repo: When set, reject a mismatched repo (fail empty).
        pr_number: When set, reject a mismatched PR.

    Returns:
        Stored state, or empty state when missing/invalid.
    """
    root = directory or LOCAL_STATE_DIR
    path = root / f"{_sanitize_key(key)}.json"
    if not path.is_file():
        return ReviewState()
    loaded = _load_artifact_file(path=path, repo=repo, pr_number=pr_number)
    return loaded if loaded is not None else ReviewState()


def local_ledger_key(*, pr_number: int | None, head_ref: str) -> str:
    """Return the local ledger key for this invocation.

    ``--pr N`` keys by PR number, never the branch name.

    Args:
        pr_number: Pull request number when reviewing a PR.
        head_ref: Branch name for a local dirty-tree review.

    Returns:
        Stable filename stem.
    """
    if pr_number is not None:
        return f"pr-{pr_number}"
    return _sanitize_key(head_ref or "HEAD")


def _sanitize_key(raw: str) -> str:
    """Replace path separators and odd characters in a ledger key."""
    cleaned = str(raw or "").strip().replace("/", "-").replace("\\", "-")
    cleaned = _UNSAFE_KEY.sub("-", cleaned).strip("-")
    return cleaned or "unnamed"


def _enforce_lru(directory: Path) -> None:
    """Delete oldest ledger files when over :data:`LOCAL_STATE_LRU`."""
    files = sorted(
        (path for path in directory.glob("*.json") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
    )
    overflow = len(files) - LOCAL_STATE_LRU
    for path in files[: max(overflow, 0)]:
        path.unlink(missing_ok=True)


def _atomic_write_json(*, path: Path, payload: dict[str, Any]) -> None:
    """Write JSON via a same-directory replace."""
    text = json.dumps(payload, indent=2, sort_keys=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name, dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.write("\n")
        tmp_path.replace(path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _load_artifact_file(
    *,
    path: Path,
    repo: str,
    pr_number: int | None,
) -> ReviewState | None:
    """Parse one artifact file, or None when it must not be trusted."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(payload, dict):
        return None
    version = payload.get("schema_version", payload.get("version"))
    if version is None:
        return None
    try:
        version_n = int(version)
    except (TypeError, ValueError):
        return None
    if version_n < 2 or version_n > ARTIFACT_STATE_VERSION:
        return None
    stored_repo = str(payload.get("repo", ""))
    if repo and stored_repo and stored_repo != repo:
        return None
    stored_pr = payload.get("pr_number")
    if pr_number is not None and stored_pr not in (None, "", pr_number):
        try:
            if int(stored_pr) != pr_number:
                return None
        except (TypeError, ValueError):
            return None
    return ReviewState.from_artifact_dict(payload)
