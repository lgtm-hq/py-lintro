"""Semantic chunking for AI diff review."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Iterable
from pathlib import PurePosixPath

from lintro.ai.review.context import split_unified_diff_by_file
from lintro.ai.review.enums.file_domain import FileDomain
from lintro.ai.review.enums.review_context_error_code import ReviewContextErrorCode
from lintro.ai.review.exceptions import ReviewContextError
from lintro.ai.review.group_labels import (
    REL_DIRECTORY_PREFIX,
    REL_SINGLE_FILE,
    REL_SOURCE_TEST,
    REL_WORKFLOW_SCRIPT_TEST,
)
from lintro.ai.review.models.chunking_result import ChunkingResult
from lintro.ai.review.models.file_classification import FileClassification
from lintro.ai.review.models.review_chunk import ReviewChunk
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.path_utils import is_test_path, matches_test_for_source
from lintro.ai.token_budget import estimate_tokens, truncate_to_budget

_LOW_PRIORITY_DOMAINS = frozenset({FileDomain.TEST, FileDomain.DOCS})
_REPETITIVE_FILE_THRESHOLD = 5
_REPETITIVE_SAMPLE_COUNT = 3


def chunk_review_context(
    *,
    context: ReviewContext,
    max_tokens: int,
    classifications: list[FileClassification],
    allow_omitted_files: bool = False,
) -> ChunkingResult:
    """Split review context into token-bounded semantic chunks.

    Args:
        context: Collected review diff context.
        max_tokens: Maximum estimated tokens per chunk diff.
        classifications: Domain classifications for changed files.
        allow_omitted_files: When False, raise when repetitive-diff sampling
            omits files instead of returning them in ``skipped_files``.

    Returns:
        Chunking result with semantic groups and any truncation warnings.

    Raises:
        ReviewContextError: When ``max_tokens`` is invalid or diff is unusable.
    """
    if max_tokens <= 0:
        raise ReviewContextError(
            f"max_tokens must be positive, got {max_tokens}",
            code=ReviewContextErrorCode.INVALID_CHUNK_BUDGET,
        )

    per_file_diffs = split_unified_diff_by_file(unified_diff=context.unified_diff)
    if not per_file_diffs:
        missing = [file.path for file in context.changed_files]
        if missing:
            raise ReviewContextError(
                "No parseable diff sections found for changed files: "
                f"{', '.join(missing)}.",
                code=ReviewContextErrorCode.NO_PARSEABLE_DIFF,
            )
        return ChunkingResult(chunks=[])

    classification_map = {item.path: item for item in classifications}
    file_paths = sorted(per_file_diffs)
    sampled_paths, sampling_notes, skipped_from_sampling = _sample_repetitive_files(
        file_paths=file_paths,
        per_file_diffs=per_file_diffs,
    )
    if skipped_from_sampling and not allow_omitted_files:
        raise ReviewContextError(
            "Repetitive identical diffs omitted files from review: "
            f"{', '.join(sorted(skipped_from_sampling))}. "
            "Pass allow_omitted_files=True to proceed with sampling.",
            code=ReviewContextErrorCode.REPETITIVE_SAMPLING_OMITTED,
        )

    semantic_groups = _build_semantic_groups(
        file_paths=sampled_paths,
        per_file_diffs=per_file_diffs,
    )

    chunks: list[ReviewChunk] = []
    warnings: list[str] = list(sampling_notes.values())
    skipped_files: list[str] = list(skipped_from_sampling)
    truncated = False
    chunk_id = 1

    for group_files, relationship in semantic_groups:
        note = _lookup_sampling_note(
            group_files=group_files,
            sampling_notes=sampling_notes,
        )
        group_diffs = {
            path: per_file_diffs[path] for path in group_files if path in per_file_diffs
        }
        split_groups = _split_group_to_budget(
            group_files=sorted(group_diffs),
            per_file_diffs=group_diffs,
            max_tokens=max_tokens,
        )

        for split_index, split_files in enumerate(split_groups):
            split_relationship = relationship
            if len(split_groups) > 1:
                split_relationship = f"{relationship} (part {split_index + 1})"

            group_chunks, was_truncated, split_warnings = _build_group_chunks(
                files=split_files,
                per_file_diffs=group_diffs,
                max_tokens=max_tokens,
                classification_map=classification_map,
                relationship=split_relationship,
                metadata_note=note,
                start_id=chunk_id,
            )
            truncated = truncated or was_truncated
            warnings.extend(split_warnings)
            chunks.extend(group_chunks)
            if group_chunks:
                chunk_id = group_chunks[-1].id + 1

    return ChunkingResult(
        chunks=chunks,
        truncated=truncated,
        warnings=warnings,
        skipped_files=sorted(set(skipped_files)),
    )


def _build_semantic_groups(
    *,
    file_paths: list[str],
    per_file_diffs: dict[str, str],
) -> list[tuple[list[str], str]]:
    """Build semantic file groups using review chunking heuristics."""
    assigned: set[str] = set()
    groups: list[tuple[list[str], str]] = []

    for group in _group_workflow_script_test(
        file_paths=file_paths,
        per_file_diffs=per_file_diffs,
    ):
        groups.append((group, REL_WORKFLOW_SCRIPT_TEST))
        assigned.update(group)

    for group in _group_source_test_pairs(
        file_paths=file_paths,
        assigned=assigned,
    ):
        groups.append((group, REL_SOURCE_TEST))
        assigned.update(group)

    for group in _group_directory_prefixes(
        file_paths=file_paths,
        assigned=assigned,
    ):
        groups.append((group, REL_DIRECTORY_PREFIX))
        assigned.update(group)

    for path in file_paths:
        if path not in assigned:
            groups.append(([path], REL_SINGLE_FILE))
            assigned.add(path)

    return groups


def _group_workflow_script_test(
    *,
    file_paths: list[str],
    per_file_diffs: dict[str, str],
) -> list[list[str]]:
    """Group CI workflows with referenced scripts and their tests."""
    workflows = [
        path for path in file_paths if PurePosixPath(path).match(".github/workflows/**")
    ]
    groups: list[list[str]] = []
    assigned_scripts: set[str] = set()

    for workflow in workflows:
        group = [workflow]
        workflow_diff = per_file_diffs.get(workflow, "")

        for path in file_paths:
            if path == workflow or path in assigned_scripts:
                continue
            if path.startswith("scripts/") and _script_referenced_in_workflow(
                script_path=path,
                workflow_diff=workflow_diff,
            ):
                group.append(path)

        for path in file_paths:
            if path in group:
                continue
            script_sources = [
                member
                for member in group
                if member.startswith("scripts/") and not is_test_path(member)
            ]
            if script_sources and _is_test_for_any(path=path, sources=script_sources):
                group.append(path)

        if len(group) > 1:
            groups.append(sorted(set(group)))
            assigned_scripts.update(
                member for member in group if member.startswith("scripts/")
            )

    return groups


def _group_source_test_pairs(
    *,
    file_paths: list[str],
    assigned: set[str],
) -> list[list[str]]:
    """Group source files with related test files."""
    groups: list[list[str]] = []
    assigned_tests: set[str] = set()

    for path in file_paths:
        if path in assigned or is_test_path(path):
            continue

        stem = PurePosixPath(path).stem
        related_tests = [
            candidate
            for candidate in file_paths
            if candidate not in assigned
            and candidate not in assigned_tests
            and matches_test_for_source(test_path=candidate, source_stem=stem)
        ]
        if related_tests:
            groups.append(sorted({path, *related_tests}))
            assigned_tests.update(related_tests)

    return groups


def _group_directory_prefixes(
    *,
    file_paths: list[str],
    assigned: set[str],
) -> list[list[str]]:
    """Group files sharing the same parent directory prefix."""
    by_parent: dict[str, list[str]] = defaultdict(list)
    for path in file_paths:
        if path in assigned:
            continue
        parent = str(PurePosixPath(path).parent)
        if parent == ".":
            continue
        by_parent[parent].append(path)

    groups: list[list[str]] = []
    for paths in by_parent.values():
        if len(paths) < 2:
            continue
        groups.append(sorted(paths))

    return groups


def _sample_repetitive_files(
    *,
    file_paths: list[str],
    per_file_diffs: dict[str, str],
) -> tuple[list[str], dict[frozenset[str], str], list[str]]:
    """Sample repetitive identical diffs, keeping three representative files."""
    signature_groups: dict[str, list[str]] = defaultdict(list)
    for path in file_paths:
        signature = _hunk_signature(per_file_diffs.get(path, ""))
        signature_groups[signature].append(path)

    sampled_paths: list[str] = []
    sampling_notes: dict[frozenset[str], str] = {}
    skipped_paths: list[str] = []

    for paths in signature_groups.values():
        if len(paths) <= _REPETITIVE_FILE_THRESHOLD:
            sampled_paths.extend(paths)
            continue

        selected = sorted(paths)[:_REPETITIVE_SAMPLE_COUNT]
        sampled_paths.extend(selected)
        omitted = sorted(set(paths) - set(selected))
        skipped_paths.extend(omitted)
        sampling_notes[frozenset(selected)] = (
            f"{len(paths)} files share identical diff hunks; "
            f"sampled {_REPETITIVE_SAMPLE_COUNT} representative files "
            f"({', '.join(omitted)} omitted)."
        )

    return sorted(set(sampled_paths)), sampling_notes, skipped_paths


def _lookup_sampling_note(
    *,
    group_files: list[str],
    sampling_notes: dict[frozenset[str], str],
) -> str | None:
    """Return a sampling note when a group contains sampled repetitive files."""
    group_set = set(group_files)
    for sampled_set, note in sampling_notes.items():
        if sampled_set.intersection(group_set):
            return note
    return None


def _split_group_to_budget(
    *,
    group_files: list[str],
    per_file_diffs: dict[str, str],
    max_tokens: int,
) -> list[list[str]]:
    """Split a semantic group into multiple file lists that fit the token budget."""
    combined_diff = _combine_diffs(
        files=group_files,
        per_file_diffs=per_file_diffs,
    )
    if estimate_tokens(combined_diff) <= max_tokens:
        return [group_files]

    if len(group_files) == 1:
        return [group_files]

    midpoint = max(1, len(group_files) // 2)
    left = group_files[:midpoint]
    right = group_files[midpoint:]
    return _split_group_to_budget(
        group_files=left,
        per_file_diffs=per_file_diffs,
        max_tokens=max_tokens,
    ) + _split_group_to_budget(
        group_files=right,
        per_file_diffs=per_file_diffs,
        max_tokens=max_tokens,
    )


def _build_group_chunks(
    *,
    files: list[str],
    per_file_diffs: dict[str, str],
    max_tokens: int,
    classification_map: dict[str, FileClassification],
    relationship: str,
    metadata_note: str | None,
    start_id: int,
) -> tuple[list[ReviewChunk], bool, list[str]]:
    """Build one or more review chunks covering all files in a semantic group."""
    ordered_files = _sort_files_by_priority(
        files=files,
        classification_map=classification_map,
    )
    combined = _combine_diffs(files=ordered_files, per_file_diffs=per_file_diffs)
    if estimate_tokens(combined) <= max_tokens:
        return (
            [
                ReviewChunk(
                    id=start_id,
                    files=ordered_files,
                    diff=combined,
                    relationship=relationship,
                    metadata_note=metadata_note,
                ),
            ],
            False,
            [],
        )

    included: list[str] = []
    included_diff = ""
    warnings: list[str] = []
    truncated = False
    chunks: list[ReviewChunk] = []
    chunk_id = start_id

    for path in ordered_files:
        candidate = _combine_diffs(
            files=[*included, path],
            per_file_diffs=per_file_diffs,
        )
        if estimate_tokens(candidate) <= max_tokens:
            included.append(path)
            included_diff = candidate
            continue

        if included:
            chunks.append(
                ReviewChunk(
                    id=chunk_id,
                    files=list(included),
                    diff=included_diff,
                    relationship=relationship,
                    metadata_note=metadata_note,
                ),
            )
            chunk_id += 1
            included = []
            included_diff = ""

        remaining_budget = max_tokens
        truncated_diff, was_truncated = truncate_to_budget(
            text=per_file_diffs[path],
            max_tokens=remaining_budget,
        )
        truncated = truncated or was_truncated
        if was_truncated:
            warnings.append(
                f"Truncated diff for {path} to fit token budget; "
                "pass paths= to collect_review_context to scope the review.",
            )
        chunks.append(
            ReviewChunk(
                id=chunk_id,
                files=[path],
                diff=truncated_diff,
                relationship=relationship,
                metadata_note=metadata_note,
            ),
        )
        chunk_id += 1

    if included:
        chunks.append(
            ReviewChunk(
                id=chunk_id,
                files=included,
                diff=included_diff,
                relationship=relationship,
                metadata_note=metadata_note,
            ),
        )

    return chunks, truncated, warnings


def _script_referenced_in_workflow(*, script_path: str, workflow_diff: str) -> bool:
    """Return True when a workflow hunk explicitly references a script path."""
    script_name = PurePosixPath(script_path).name
    patterns = (
        re.compile(rf"\brun:\s*\S*{re.escape(script_name)}\b"),
        re.compile(rf"\buses:\s*\.?/?{re.escape(script_path)}\b"),
        re.compile(rf"\b{re.escape(script_path)}\b"),
    )
    for line in workflow_diff.splitlines():
        if not line.startswith(("+", "-")) or line.startswith(("+++", "---")):
            continue
        content = line[1:]
        if any(pattern.search(content) for pattern in patterns):
            return True
    return False


def _sort_files_by_priority(
    *,
    files: Iterable[str],
    classification_map: dict[str, FileClassification],
) -> list[str]:
    """Sort files so higher-signal domains appear before tests and docs."""
    return sorted(
        files,
        key=lambda path: (
            -_file_priority(
                classification=classification_map.get(path),
            ),
            path,
        ),
    )


def _file_priority(*, classification: FileClassification | None) -> int:
    """Return a priority score where larger values are higher signal."""
    if classification is None:
        return 1

    domains = set(classification.domains)
    if FileDomain.SECURITY in domains:
        return 5
    if FileDomain.TEST in domains:
        return 1
    if domains - _LOW_PRIORITY_DOMAINS:
        return 3
    return 0


def _combine_diffs(
    *,
    files: list[str],
    per_file_diffs: dict[str, str],
) -> str:
    """Combine per-file diffs in path order."""
    parts = [per_file_diffs[path] for path in files if path in per_file_diffs]
    return "".join(parts)


def _hunk_signature(diff_text: str) -> str:
    """Hash normalized hunk bodies to detect repetitive changes."""
    hunk_lines: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("@@"):
            continue
        if line.startswith(("+", "-", " ")):
            if line.startswith(("+++", "---")):
                continue
            hunk_lines.append(line)
    normalized = "\n".join(hunk_lines)
    if not normalized:
        normalized = diff_text
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _is_test_for_any(*, path: str, sources: list[str]) -> bool:
    """Return True when ``path`` is a test for any source in ``sources``."""
    return any(
        matches_test_for_source(
            test_path=path,
            source_stem=PurePosixPath(source).stem,
        )
        for source in sources
        if not is_test_path(source)
    )
