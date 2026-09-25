"""Versioned review state for workflow artifacts and leftover blobs (#2154)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.github_constants import STATE_VERSION
from lintro.ai.review.models.coverage_record import CoverageRecord
from lintro.ai.review.models.finding_record import FindingRecord, rebaseline_records
from lintro.ai.review.models.flagged_file import FlaggedFile
from lintro.ai.review.models.run_record import RunRecord

__all__ = ["ReviewState", "spend_from_payload"]


@dataclass(frozen=True, slots=True)
class ReviewState:
    """Machine-readable review history for one pull request.

    Authoritative CI state lives in workflow artifacts (schema 3). A schema 2
    blob left behind on an older sticky comment still decodes, so a round can
    recover runs and findings from it; coverage is never seeded from a
    comment, and a v1 blob is not read at all (#2305).

    Attributes:
        version: Schema version of the decoded payload.
        runs: Per-round statistics, oldest first.
        findings: Tracked findings (open and resolved) across all rounds.
        coverage: File-level coverage records keyed ``(path, hash)``.
        flagged_files: Guarded reviewer re-read requests.
        pending_invalidations: Unserved group/import re-reads, as
            ``(path, need)`` pairs. Survives a capped round so the next
            empty push still queues those files without round livelock.
        consumed_flags: ``(path, hash)`` pairs already honored once so a
            repeat flag cannot re-queue the same unchanged file.
        repo: ``owner/name`` that produced the state.
        pr_number: Pull request number, or ``None`` for a local branch run.
        base_sha: Base commit when the state was written.
        head_sha: Head commit when the state was written.
        workflow: Trusted workflow filename (CI only).
        event: Workflow event (CI only).
        run_id: Actions run id that wrote the state.
        lintro_version: Lintro version that wrote the state.
        truncated: True when older runs or resolved findings were pruned.
        pr_spend_usd: The PR's cumulative review spend in USD (#2796): every
            round's cost, including a round interrupted after a checkpoint.
            Only ever grows; run pruning never lowers it. Read it through
            :attr:`review_spend_usd`.
    """

    version: int = STATE_VERSION
    runs: tuple[RunRecord, ...] = field(default_factory=tuple)
    findings: tuple[FindingRecord, ...] = field(default_factory=tuple)
    coverage: tuple[CoverageRecord, ...] = field(default_factory=tuple)
    flagged_files: tuple[FlaggedFile, ...] = field(default_factory=tuple)
    pending_invalidations: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    consumed_flags: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    repo: str = ""
    pr_number: int | None = None
    base_sha: str = ""
    head_sha: str = ""
    workflow: str = ""
    event: str = ""
    run_id: str = ""
    lintro_version: str = ""
    truncated: bool = False
    pr_spend_usd: float = 0.0

    @property
    def review_spend_usd(self) -> float:
        """Return the PR's review spend, never below the retained runs' sum.

        A state built without the cumulative total (a pre-v5 artifact, a
        legacy sticky blob, a hand-built state) still counts its runs.
        """
        return max(self.pr_spend_usd, sum(run.usage.cost for run in self.runs))

    @property
    def next_round(self) -> int:
        """Return the round number the next review run should record."""
        if not self.runs:
            return 1
        return max(run.identity.round for run in self.runs) + 1

    @property
    def open_findings(self) -> tuple[FindingRecord, ...]:
        """Return the currently open findings, in tracking order."""
        return tuple(
            record for record in self.findings if record.status is FindingStatus.OPEN
        )

    @property
    def resolved_findings(self) -> tuple[FindingRecord, ...]:
        """Return the findings already marked resolved."""
        return tuple(
            record
            for record in self.findings
            if record.status is FindingStatus.RESOLVED
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the state for the hidden comment blob.

        Returns:
            JSON-serializable mapping with ``version``, ``runs``, and
            ``findings`` keys (plus ``truncated`` when pruning occurred).
        """
        payload: dict[str, Any] = {
            "version": self.version,
            "runs": [run.to_dict() for run in self.runs],
            "findings": [record.to_dict() for record in self.findings],
        }
        if self.truncated:
            payload["truncated"] = True
        if self.review_spend_usd:
            payload["pr_spend_usd"] = self.review_spend_usd
        return payload

    def to_artifact_dict(self) -> dict[str, Any]:
        """Serialize the schema-3 artifact envelope.

        Returns:
            JSON-serializable mapping with identity metadata and coverage.
        """
        payload: dict[str, Any] = {
            "schema_version": STATE_VERSION,
            "version": STATE_VERSION,
            "repo": self.repo,
            "pr_number": self.pr_number,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "workflow": self.workflow,
            "event": self.event,
            "run_id": self.run_id,
            "lintro_version": self.lintro_version,
            "runs": [run.to_dict() for run in self.runs],
            "findings": [record.to_dict() for record in self.findings],
            "coverage": [record.to_dict() for record in self.coverage],
            "flagged_files": [flag.to_dict() for flag in self.flagged_files],
            "pending_invalidations": [
                {"path": path, "need": need}
                for path, need in self.pending_invalidations
            ],
            "consumed_flags": [
                {"path": path, "hash": patch_hash}
                for path, patch_hash in self.consumed_flags
            ],
            "pr_spend_usd": self.review_spend_usd,
        }
        if self.truncated:
            payload["truncated"] = True
        return payload

    @classmethod
    def from_artifact_dict(cls, payload: dict[str, Any]) -> ReviewState:
        """Parse a schema-3 envelope; unknown coverage rows are dropped.

        Args:
            payload: Decoded JSON mapping.

        Returns:
            Parsed state. Invalid coverage/flag rows are omitted.
        """
        coverage: list[CoverageRecord] = []
        for raw in payload.get("coverage") or []:
            if isinstance(raw, dict):
                coverage_row = CoverageRecord.from_dict(raw)
                if coverage_row is not None:
                    coverage.append(coverage_row)
        flagged: list[FlaggedFile] = []
        for raw in payload.get("flagged_files") or []:
            if isinstance(raw, dict):
                flag_row = FlaggedFile.from_dict(raw)
                if flag_row is not None:
                    flagged.append(flag_row)
        pr_raw = payload.get("pr_number")
        pr_number: int | None
        try:
            pr_number = int(pr_raw) if pr_raw not in (None, "") else None
        except (TypeError, ValueError):
            pr_number = None
        return cls(
            version=STATE_VERSION,
            runs=tuple(
                RunRecord.from_dict(item)
                for item in payload.get("runs") or []
                if isinstance(item, dict)
            ),
            findings=rebaseline_records(
                records=[
                    record
                    for item in payload.get("findings") or []
                    if isinstance(item, dict)
                    for record in (FindingRecord.from_dict(item),)
                    if record is not None
                ],
                version=_payload_version(payload),
            ),
            coverage=tuple(coverage),
            flagged_files=tuple(flagged),
            pending_invalidations=_pending_from_payload(payload),
            consumed_flags=_consumed_from_payload(payload),
            repo=str(payload.get("repo", "")),
            pr_number=pr_number,
            base_sha=str(payload.get("base_sha", "")),
            head_sha=str(payload.get("head_sha", "")),
            workflow=str(payload.get("workflow", "")),
            event=str(payload.get("event", "")),
            run_id=str(payload.get("run_id", "")),
            lintro_version=str(payload.get("lintro_version", "")),
            truncated=bool(payload.get("truncated", False)),
            pr_spend_usd=spend_from_payload(payload),
        )


def spend_from_payload(payload: dict[str, Any]) -> float:
    """Return the stored cumulative spend, or 0.0 when absent or invalid.

    A v4 artifact has no ``pr_spend_usd``; :attr:`ReviewState.review_spend_usd`
    then seeds the total from the runs that survived pruning.

    Args:
        payload: Decoded artifact mapping.

    Returns:
        A finite, non-negative USD figure.
    """
    raw = payload.get("pr_spend_usd")
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return 0.0
    value = float(raw)
    return value if math.isfinite(value) and value >= 0 else 0.0


def _pending_from_payload(
    payload: dict[str, Any],
) -> tuple[tuple[str, str], ...]:
    """Parse pending invalidation pairs from an artifact mapping."""
    raw = payload.get("pending_invalidations") or []
    parsed: list[tuple[str, str]] = []
    if not isinstance(raw, list):
        return ()
    allowed = {"group_invalidated", "import_invalidated"}
    for item in raw:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path", "")).strip()
        need = str(item.get("need", "")).strip()
        if path and need in allowed:
            parsed.append((path, need))
    return tuple(parsed)


def _consumed_from_payload(
    payload: dict[str, Any],
) -> tuple[tuple[str, str], ...]:
    """Parse honored flag identities from an artifact mapping."""
    raw = payload.get("consumed_flags") or []
    parsed: list[tuple[str, str]] = []
    if not isinstance(raw, list):
        return ()
    for item in raw:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path", "")).strip()
        patch_hash = str(item.get("hash", "")).strip()
        if path and patch_hash:
            parsed.append((path, patch_hash))
    return tuple(parsed)


def _payload_version(payload: dict[str, Any]) -> int:
    """Return the schema version a persisted payload was written under.

    Args:
        payload: The decoded artifact or sticky payload.

    Returns:
        The integer version, or ``0`` when absent or unreadable, which no
        re-baseline rule matches.
    """
    raw = payload.get("schema_version", payload.get("version"))
    if raw is None or isinstance(raw, bool):
        return 0
    try:
        # The artifact store accepts a coercible version ("3"); read it the
        # same way so an accepted pre-v4 artifact is re-baselined.
        return int(raw)
    except (TypeError, ValueError):
        return 0
