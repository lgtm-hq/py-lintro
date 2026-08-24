"""File-level coverage classification and queueing (#2154)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from lintro.ai.review.enums.changed_file_status import ChangedFileStatus
from lintro.ai.review.enums.file_review_need import (
    FILE_REVIEW_NEED_PRIORITY,
    FileReviewNeed,
)
from lintro.ai.review.enums.file_skip_reason import FileSkipReason
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.coverage_counts import CoverageCounts
from lintro.ai.review.models.coverage_record import CoverageRecord
from lintro.ai.review.models.flagged_file import FlaggedFile
from lintro.ai.review.models.skipped_file import SkippedFile
from lintro.ai.review.patch_hash import normalized_patch_hash

__all__ = [
    "BROADCAST_FILENAMES",
    "MAX_FLAGS_PER_ROUND",
    "ClassifiedFile",
    "classify_files",
    "coverage_counts",
    "directly_changed_paths",
    "inherit_same_round_paths",
    "latest_coverage_by_path",
    "pending_invalidations_for",
    "queue_paths",
    "review_eligible_paths",
]

#: Paths that never fan out invalidation (ADR-0007). ``conftest.py`` is a
#: known semantic hole: test-wide fixtures can change behavior without
#: re-entering dependents. Covered by groups and model flags; revisit list.
BROADCAST_FILENAMES = frozenset(
    {
        "pyproject.toml",
        "poetry.lock",
        "uv.lock",
        "Pipfile.lock",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "Cargo.lock",
        "conftest.py",
    },
)

MAX_FLAGS_PER_ROUND = 8


@dataclass(frozen=True, slots=True)
class ClassifiedFile:
    """A review-eligible file and why it is (or is not) queued.

    Attributes:
        path: Repository-relative path.
        patch_hash: Current normalized patch hash.
        need: Classification for this round.
        flag_reason: Reviewer reason when ``need`` is model-flagged.
    """

    path: str
    patch_hash: str
    need: FileReviewNeed
    flag_reason: str = ""


def review_eligible_paths(
    *,
    changed_files: Sequence[ChangedFile],
    skipped: Sequence[SkippedFile] = (),
) -> tuple[str, ...]:
    """Return paths that count toward 100% coverage.

    Deletions leave the denominator (their findings resolve as a delete).
    Path/config/agent-scope skips never make completion impossible.
    Repetitive-diff samples stay eligible so an identical-hash sibling
    can inherit coverage.

    Args:
        changed_files: Files in the current diff.
        skipped: Files dropped by selection or chunking.

    Returns:
        Sorted eligible paths.
    """
    skip_reasons = {entry.path: entry.reason for entry in skipped}
    eligible: list[str] = []
    for changed in changed_files:
        status = (
            changed.status
            if isinstance(changed.status, ChangedFileStatus)
            else ChangedFileStatus(str(changed.status))
        )
        if status is ChangedFileStatus.DELETED:
            continue
        reason = skip_reasons.get(changed.path)
        if reason in {
            FileSkipReason.PATH_FILTER,
            FileSkipReason.CONFIG_EXCLUDED,
            FileSkipReason.AGENT_SCOPE,
        }:
            continue
        eligible.append(changed.path)
    return tuple(sorted(set(eligible)))


def classify_files(
    *,
    eligible_paths: Sequence[str],
    current_hashes: Mapping[str, str],
    coverage: Sequence[CoverageRecord],
    groups: Sequence[Sequence[str]] = (),
    import_importers: Mapping[str, set[str]] | None = None,
    flags: Sequence[FlaggedFile] = (),
    pending_invalidations: Sequence[tuple[str, str]] = (),
    force_full: bool = False,
) -> tuple[ClassifiedFile, ...]:
    """Classify each eligible file for this resume round.

    Args:
        eligible_paths: Review-eligible paths at HEAD.
        current_hashes: Current normalized patch hash per path.
        coverage: Prior coverage records (possibly empty).
        groups: Semantic groups (each a sequence of paths).
        import_importers: ``{directly_changed: {importer, ...}}``.
        flags: Guarded reviewer flags from prior rounds.
        pending_invalidations: Unserved group/import pairs from a prior
            capped round.
        force_full: When True, treat every file as never-reviewed.

    Returns:
        One classification per eligible path, in path order.
    """
    by_path = latest_coverage_by_path(coverage)
    covered_hashes = {(record.path, record.patch_hash) for record in coverage}
    if force_full:
        return tuple(
            ClassifiedFile(
                path=path,
                patch_hash=current_hashes.get(path, ""),
                need=FileReviewNeed.NEVER_REVIEWED,
            )
            for path in eligible_paths
        )

    directly_changed: set[str] = set()
    never_reviewed: set[str] = set()
    for path in eligible_paths:
        current = current_hashes.get(path, "")
        prior = by_path.get(path)
        if prior is None:
            never_reviewed.add(path)
        elif prior.patch_hash != current:
            directly_changed.add(path)

    sampled_covered = _inherit_sampled_hashes(
        eligible_paths=eligible_paths,
        current_hashes=current_hashes,
        covered_hashes=covered_hashes,
    )

    broadcast = _broadcast_paths(eligible_paths)
    group_invalidated = _group_invalidated(
        eligible_paths=eligible_paths,
        groups=groups,
        directly_changed=directly_changed,
        broadcast=broadcast,
    )
    pending_group, pending_import = _pending_sets(pending_invalidations)
    group_invalidated.update(pending_group)
    graph = import_importers or {}
    import_invalidated: set[str] = set()
    for imported in directly_changed:
        import_invalidated.update(graph.get(imported, set()))
    import_invalidated.update(pending_import)

    allowed_flags = _allowed_flags(
        flags=flags,
        eligible_paths=set(eligible_paths),
        current_hashes=current_hashes,
        covered_hashes=covered_hashes,
    )

    classified: list[ClassifiedFile] = []
    for path in eligible_paths:
        current = current_hashes.get(path, "")
        if path in never_reviewed and path not in sampled_covered:
            need = FileReviewNeed.NEVER_REVIEWED
            reason = ""
        elif path in directly_changed:
            need = FileReviewNeed.DIRECTLY_CHANGED
            reason = ""
        elif path in allowed_flags:
            need = FileReviewNeed.MODEL_FLAGGED
            reason = allowed_flags[path]
        elif path in group_invalidated:
            need = FileReviewNeed.GROUP_INVALIDATED
            reason = ""
        elif path in import_invalidated:
            need = FileReviewNeed.IMPORT_INVALIDATED
            reason = ""
        else:
            need = FileReviewNeed.COVERED
            reason = ""
        classified.append(
            ClassifiedFile(
                path=path,
                patch_hash=current,
                need=need,
                flag_reason=reason,
            ),
        )
    return tuple(classified)


def queue_paths(*, classified: Sequence[ClassifiedFile]) -> tuple[str, ...]:
    """Return files that need review, in cap-safe queue order.

    Args:
        classified: Output of :func:`classify_files`.

    Returns:
        Paths excluding ``COVERED``, sorted by priority then path.
    """
    pending = [item for item in classified if item.need is not FileReviewNeed.COVERED]
    pending.sort(
        key=lambda item: (
            FILE_REVIEW_NEED_PRIORITY[item.need],
            item.path,
        ),
    )
    return tuple(item.path for item in pending)


def coverage_counts(
    *,
    classified: Sequence[ClassifiedFile],
    reviewed_now: Iterable[str],
) -> CoverageCounts:
    """Build per-round coverage counters.

    Args:
        classified: This round's classification.
        reviewed_now: Paths the provider actually read.

    Returns:
        Counters for the JSON envelope and sticky Variant B.
    """
    reviewed_set = set(reviewed_now)
    reviewed = 0
    carried = 0
    awaiting = 0
    invalidated = 0
    for item in classified:
        if item.need is FileReviewNeed.COVERED:
            carried += 1
            continue
        if item.path in reviewed_set:
            reviewed += 1
            if item.need in {
                FileReviewNeed.GROUP_INVALIDATED,
                FileReviewNeed.IMPORT_INVALIDATED,
                FileReviewNeed.MODEL_FLAGGED,
            }:
                invalidated += 1
            continue
        awaiting += 1
        if item.need in {
            FileReviewNeed.GROUP_INVALIDATED,
            FileReviewNeed.IMPORT_INVALIDATED,
            FileReviewNeed.MODEL_FLAGGED,
        }:
            invalidated += 1
    return CoverageCounts(
        reviewed=reviewed,
        carried=carried,
        awaiting=awaiting,
        invalidated=invalidated,
        eligible=len(classified),
    )


def directly_changed_paths(
    *,
    eligible_paths: Sequence[str],
    current_hashes: Mapping[str, str],
    coverage: Sequence[CoverageRecord],
) -> set[str]:
    """Return paths whose latest stored hash differs from HEAD.

    Uses the highest-round record per path so a later review of a new
    hash does not keep treating the file as changed.

    Args:
        eligible_paths: Review-eligible paths at HEAD.
        current_hashes: Current normalized patch hash per path.
        coverage: Prior coverage records.

    Returns:
        Paths that are directly changed this round.
    """
    latest = latest_coverage_by_path(coverage)
    changed: set[str] = set()
    for path in eligible_paths:
        prior = latest.get(path)
        if prior is not None and prior.patch_hash != current_hashes.get(path, ""):
            changed.add(path)
    return changed


def pending_invalidations_for(
    *,
    classified: Sequence[ClassifiedFile],
    reviewed_now: Iterable[str],
) -> tuple[tuple[str, str], ...]:
    """Return unserved group/import pairs to persist for the next round.

    Args:
        classified: This round's classification.
        reviewed_now: Paths the provider actually read (not inherited).

    Returns:
        ``(path, need)`` pairs still awaiting a real review.
    """
    reviewed = set(reviewed_now)
    return tuple(
        (item.path, item.need.value)
        for item in classified
        if item.need
        in {
            FileReviewNeed.GROUP_INVALIDATED,
            FileReviewNeed.IMPORT_INVALIDATED,
        }
        and item.path not in reviewed
    )


def inherit_same_round_paths(
    *,
    reviewed_now: Sequence[str],
    eligible_paths: Sequence[str],
    current_hashes: Mapping[str, str],
) -> tuple[str, ...]:
    """Credit sampled siblings that share a reviewed representative's hash.

    Args:
        reviewed_now: Paths the provider actually read.
        eligible_paths: Review-eligible paths at HEAD.
        current_hashes: Current normalized patch hash per path.

    Returns:
        ``reviewed_now`` plus same-hash siblings, de-duplicated.
    """
    reviewed = list(dict.fromkeys(reviewed_now))
    reviewed_hashes = {
        current_hashes.get(path, "")
        for path in reviewed
        if current_hashes.get(path, "")
    }
    extras = [
        path
        for path in eligible_paths
        if path not in reviewed
        and current_hashes.get(path, "") in reviewed_hashes
        and current_hashes.get(path, "")
    ]
    return (*reviewed, *extras)


def latest_coverage_by_path(
    coverage: Sequence[CoverageRecord],
) -> dict[str, CoverageRecord]:
    """Keep the highest-round record per path."""
    latest: dict[str, CoverageRecord] = {}
    for record in coverage:
        current = latest.get(record.path)
        if current is None or record.round >= current.round:
            latest[record.path] = record
    return latest


def hashes_for_diffs(*, diffs: Mapping[str, str]) -> dict[str, str]:
    """Hash each per-file unified diff.

    Args:
        diffs: Path to unified diff text.

    Returns:
        Path to normalized patch hash.
    """
    return {path: normalized_patch_hash(text) for path, text in diffs.items()}


def _inherit_sampled_hashes(
    *,
    eligible_paths: Sequence[str],
    current_hashes: Mapping[str, str],
    covered_hashes: set[tuple[str, str]],
) -> set[str]:
    """Mark files whose hash already has a reviewed representative."""
    reviewed_hashes = {patch_hash for _, patch_hash in covered_hashes}
    inherited: set[str] = set()
    covered_paths = {path for path, _ in covered_hashes}
    for path in eligible_paths:
        if path in covered_paths:
            continue
        patch_hash = current_hashes.get(path, "")
        if patch_hash and patch_hash in reviewed_hashes:
            inherited.add(path)
    return inherited


def _group_invalidated(
    *,
    eligible_paths: Sequence[str],
    groups: Sequence[Sequence[str]],
    directly_changed: set[str],
    broadcast: set[str],
) -> set[str]:
    """Return covered group-mates of a non-broadcast changed file."""
    if not directly_changed:
        return set()
    changed_triggers = directly_changed - broadcast
    invalidated: set[str] = set()
    eligible = set(eligible_paths)
    for group in groups:
        members = set(group) & eligible
        if members & changed_triggers:
            invalidated.update(members - directly_changed)
    return invalidated


def _pending_sets(
    pending_invalidations: Sequence[tuple[str, str]],
) -> tuple[set[str], set[str]]:
    """Split persisted pending pairs into group and import path sets."""
    group: set[str] = set()
    imports: set[str] = set()
    for path, need in pending_invalidations:
        if need == FileReviewNeed.GROUP_INVALIDATED:
            group.add(path)
        elif need == FileReviewNeed.IMPORT_INVALIDATED:
            imports.add(path)
    return group, imports


def _broadcast_paths(paths: Sequence[str]) -> set[str]:
    """Return members whose basename is a broadcast filename."""
    return {path for path in paths if path.rsplit("/", 1)[-1] in BROADCAST_FILENAMES}


def _allowed_flags(
    *,
    flags: Sequence[FlaggedFile],
    eligible_paths: set[str],
    current_hashes: Mapping[str, str],
    covered_hashes: set[tuple[str, str]],
) -> dict[str, str]:
    """Allowlist, one-way, cap, and de-dupe reviewer flags."""
    accepted: dict[str, str] = {}
    seen_keys: set[tuple[str, str]] = set()
    for flag in flags:
        if len(accepted) >= MAX_FLAGS_PER_ROUND:
            break
        if flag.path not in eligible_paths:
            continue
        current = current_hashes.get(flag.path, "")
        patch_hash = flag.patch_hash or current
        key = (flag.path, patch_hash)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        # One-way: only a covered (path, hash) can be pushed back.
        if (flag.path, current) not in covered_hashes:
            continue
        accepted[flag.path] = flag.reason
    return accepted
