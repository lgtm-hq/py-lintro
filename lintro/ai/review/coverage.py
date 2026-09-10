"""File-level coverage classification and queueing (#2154)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from lintro.ai.review.coverage_rounds import (
    carry_unserved_flags,
    consume_served_flags,
    hashes_for_diffs,
    inherit_same_round_paths,
    latest_coverage_by_path,
)
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

__all__ = [
    "BROADCAST_FILENAMES",
    "MAX_FLAGS_PER_ROUND",
    "ClassifiedFile",
    "ClassifyFilesRequest",
    "classify_files",
    "coverage_counts",
    "directly_changed_paths",
    "inherit_same_round_paths",
    "latest_coverage_by_path",
    "carry_unserved_flags",
    "consume_served_flags",
    "pending_invalidations_for",
    "hashes_for_diffs",
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


@dataclass(frozen=True, slots=True, kw_only=True)
class ClassifyFilesRequest:
    """Everything one resume round's classification reads.

    Grouping the round's inputs keeps :func:`classify_files` to one argument
    and makes a new input a field here rather than another keyword threaded
    through every caller (issue #2301).

    Attributes:
        eligible_paths: Review-eligible paths at HEAD.
        current_hashes: Current normalized patch hash per path.
        coverage: Prior coverage records (possibly empty).
        groups: Semantic groups (each a sequence of paths).
        import_importers: ``{directly_changed: {importer, ...}}``.
        flags: Guarded reviewer flags from prior rounds.
        pending_invalidations: Unserved group/import pairs from a prior
            capped round.
        consumed_flags: ``(path, hash)`` pairs already honored once.
        force_full: When True, treat every file as never-reviewed.
    """

    eligible_paths: Sequence[str]
    current_hashes: Mapping[str, str]
    coverage: Sequence[CoverageRecord]
    groups: Sequence[Sequence[str]] = ()
    import_importers: Mapping[str, set[str]] | None = None
    flags: Sequence[FlaggedFile] = ()
    pending_invalidations: Sequence[tuple[str, str]] = ()
    consumed_flags: Sequence[tuple[str, str]] = ()
    force_full: bool = False


def classify_files(*, request: ClassifyFilesRequest) -> tuple[ClassifiedFile, ...]:
    """Classify each eligible file for this resume round.

    Args:
        request: The round's inputs.

    Returns:
        One classification per eligible path, in path order.
    """
    eligible_paths = request.eligible_paths
    current_hashes = request.current_hashes

    by_path = latest_coverage_by_path(request.coverage)
    covered_hashes = {(record.path, record.patch_hash) for record in request.coverage}
    if request.force_full:
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
        groups=request.groups,
        directly_changed=directly_changed,
        broadcast=broadcast,
    )
    pending_group, pending_import = _pending_sets(request.pending_invalidations)
    group_invalidated.update(pending_group)
    graph = request.import_importers or {}
    import_invalidated: set[str] = set()
    for imported in directly_changed:
        import_invalidated.update(graph.get(imported, set()))
    import_invalidated.update(pending_import)

    allowed_flags = _allowed_flags(
        flags=request.flags,
        eligible_paths=set(eligible_paths),
        current_hashes=current_hashes,
        covered_hashes=covered_hashes,
        consumed_flags=request.consumed_flags,
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
        reviewed_now: Paths covered this round, including same-hash
            siblings of a provider-read representative.

    Returns:
        ``(path, need)`` pairs still awaiting coverage.
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
    consumed_flags: Sequence[tuple[str, str]] = (),
) -> dict[str, str]:
    """Allowlist, one-way, cap, and de-dupe reviewer flags."""
    accepted: dict[str, str] = {}
    seen_keys: set[tuple[str, str]] = set()
    consumed = set(consumed_flags)
    for flag in flags:
        if len(accepted) >= MAX_FLAGS_PER_ROUND:
            break
        if flag.path not in eligible_paths:
            continue
        current = current_hashes.get(flag.path, "")
        patch_hash = flag.patch_hash or current
        key = (flag.path, patch_hash)
        if key in seen_keys or key in consumed:
            continue
        seen_keys.add(key)
        # One-way: only a covered (path, hash) can be pushed back.
        if (flag.path, current) not in covered_hashes:
            continue
        accepted[flag.path] = flag.reason
    return accepted
