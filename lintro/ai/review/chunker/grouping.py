"""Semantic grouping and token-budget chunking for AI diff review."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable
from pathlib import PurePosixPath

from lintro.ai.review.chunker.workflow_scripts import (
    _is_workflow_linked_script,
    _script_referenced_in_workflow,
    _workflow_text_for_matching,
)
from lintro.ai.review.context import split_unified_diff_by_file
from lintro.ai.review.enums.file_domain import FileDomain
from lintro.ai.review.enums.review_context_error_code import ReviewContextErrorCode
from lintro.ai.review.exceptions import ReviewContextError
from lintro.ai.review.glob_utils import path_matches_any_glob
from lintro.ai.review.group_labels import (
    REL_DIRECTORY_PREFIX,
    REL_SINGLE_FILE,
    REL_SOURCE_TEST,
    REL_WORKFLOW_SCRIPT_TEST,
    RelationshipLabel,
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
    allow_omitted_files: bool = True,
) -> ChunkingResult:
    """Split review context into token-bounded semantic chunks.

    Args:
        context: Collected review diff context.
        max_tokens: Maximum estimated tokens per chunk diff.
        classifications: Domain classifications for changed files.
        allow_omitted_files: When True (default), return omitted repetitive-diff
            files in ``skipped_files`` instead of raising. Pass False for strict
            behavior.

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

    missing_diffs = [
        changed_file.path
        for changed_file in context.changed_files
        if changed_file.path not in per_file_diffs
    ]
    if missing_diffs:
        raise ReviewContextError(
            f"Changed files missing diff sections: {', '.join(missing_diffs)}.",
            code=ReviewContextErrorCode.DIFF_DESYNC,
        )

    changed_paths = {changed_file.path for changed_file in context.changed_files}
    extra_diffs = sorted(set(per_file_diffs) - changed_paths)
    if extra_diffs:
        raise ReviewContextError(
            "Diff sections found for files not in changed_files: "
            f"{', '.join(extra_diffs)}.",
            code=ReviewContextErrorCode.DIFF_DESYNC,
        )

    classification_map = {item.path: item for item in classifications}
    file_paths = sorted(changed_paths)
    semantic_groups_full = _build_semantic_groups(
        file_paths=file_paths,
        per_file_diffs=per_file_diffs,
        post_image_files=context.post_image_files,
    )
    protected_paths = {
        path
        for group_files, relationship in semantic_groups_full
        if relationship in {REL_WORKFLOW_SCRIPT_TEST, REL_SOURCE_TEST}
        for path in group_files
    }
    sampling_candidates = [path for path in file_paths if path not in protected_paths]
    sampled_paths, sampling_notes, skipped_from_sampling, sampling_warnings = (
        _sample_repetitive_files(
            file_paths=sampling_candidates,
            per_file_diffs=per_file_diffs,
            classification_map=classification_map,
        )
    )
    warnings: list[str] = _unreferenced_workflow_script_warnings(
        file_paths=[path for path in file_paths if path not in skipped_from_sampling],
        per_file_diffs=per_file_diffs,
        post_image_files=context.post_image_files,
    )
    if skipped_from_sampling and not allow_omitted_files:
        raise ReviewContextError(
            "Repetitive identical diffs omitted files from review: "
            f"{', '.join(sorted(skipped_from_sampling))}. "
            "Pass allow_omitted_files=True to proceed with sampling.",
            code=ReviewContextErrorCode.REPETITIVE_SAMPLING_OMITTED,
        )

    final_paths = sorted(protected_paths | set(sampled_paths))
    final_path_set = set(final_paths)
    semantic_groups = _prune_semantic_groups(
        groups=semantic_groups_full,
        remaining_paths=final_path_set,
    )

    chunks: list[ReviewChunk] = []
    warnings.extend(sampling_warnings)
    skipped_files: list[str] = list(skipped_from_sampling)
    truncated = False
    chunk_id = 1

    for group_files, relationship in semantic_groups:
        group_diffs = {
            path: per_file_diffs[path] for path in group_files if path in per_file_diffs
        }
        split_groups = _split_group_to_budget(
            group_files=sorted(group_diffs),
            per_file_diffs=group_diffs,
            max_tokens=max_tokens,
            classification_map=classification_map,
        )

        for split_files in split_groups:
            note = _lookup_sampling_note(
                group_files=split_files,
                sampling_notes=sampling_notes,
            )
            group_chunks, was_truncated, split_warnings = _build_group_chunks(
                files=split_files,
                per_file_diffs=group_diffs,
                max_tokens=max_tokens,
                classification_map=classification_map,
                relationship=relationship,
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
    post_image_files: dict[str, str],
) -> list[tuple[list[str], RelationshipLabel]]:
    """Build semantic file groups using review chunking heuristics."""
    assigned: set[str] = set()
    groups: list[tuple[list[str], RelationshipLabel]] = []

    for group in _group_workflow_script_test(
        file_paths=file_paths,
        per_file_diffs=per_file_diffs,
        post_image_files=post_image_files,
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


def _prune_semantic_groups(
    *,
    groups: list[tuple[list[str], RelationshipLabel]],
    remaining_paths: set[str],
) -> list[tuple[list[str], RelationshipLabel]]:
    """Keep established semantic groups after repetitive-diff sampling."""
    pruned: list[tuple[list[str], RelationshipLabel]] = []
    covered: set[str] = set()
    for group_files, relationship in groups:
        kept = sorted(path for path in group_files if path in remaining_paths)
        if not kept:
            continue
        covered.update(kept)
        if len(kept) == 1:
            pruned.append((kept, REL_SINGLE_FILE))
        else:
            pruned.append((kept, relationship))
    for path in sorted(remaining_paths - covered):
        pruned.append(([path], REL_SINGLE_FILE))
    return pruned


def _group_workflow_script_test(
    *,
    file_paths: list[str],
    per_file_diffs: dict[str, str],
    post_image_files: dict[str, str],
) -> list[list[str]]:
    """Group CI workflows with referenced scripts and their tests."""
    workflows = [
        path
        for path in file_paths
        if path_matches_any_glob(path=path, patterns=(".github/workflows/**",))
    ]
    if not workflows:
        return []

    workflow_scripts: dict[str, set[str]] = {}
    for workflow in workflows:
        workflow_diff = per_file_diffs.get(workflow, "")
        workflow_scripts[workflow] = _referenced_scripts_in_workflow(
            workflow_path=workflow,
            workflow_diff=workflow_diff,
            post_image_files=post_image_files,
            file_paths=file_paths,
        )

    parent = {workflow: workflow for workflow in workflows}

    def _find(node: str) -> str:
        root = node
        while parent[root] != root:
            parent[root] = parent[parent[root]]
            root = parent[root]
        return root

    def _union(left: str, right: str) -> None:
        left_root = _find(left)
        right_root = _find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    script_owners: dict[str, str] = {}
    for workflow, scripts in workflow_scripts.items():
        for script in scripts:
            owner = script_owners.get(script)
            if owner is None:
                script_owners[script] = workflow
            else:
                _union(owner, workflow)

    components: dict[str, set[str]] = defaultdict(set)
    for workflow in workflows:
        root = _find(workflow)
        components[root].add(workflow)
        components[root].update(workflow_scripts[workflow])

    groups: list[list[str]] = []
    assigned_tests: set[str] = set()
    for member_paths in components.values():
        group = set(member_paths)
        script_sources = [
            path for path in group if _is_workflow_linked_script(path=path)
        ]
        for path in file_paths:
            if path in group or path in assigned_tests:
                continue
            if script_sources and _is_test_for_any(path=path, sources=script_sources):
                group.add(path)
                assigned_tests.add(path)
        if len(group) > 1:
            groups.append(sorted(group))

    return groups


def _referenced_scripts_in_workflow(
    *,
    workflow_path: str,
    workflow_diff: str,
    post_image_files: dict[str, str],
    file_paths: list[str],
) -> set[str]:
    """Return script paths explicitly referenced in a workflow."""
    workflow_text = _workflow_text_for_matching(
        workflow_path=workflow_path,
        workflow_diff=workflow_diff,
        post_image_files=post_image_files,
    )
    return {
        path
        for path in file_paths
        if _is_workflow_linked_script(path=path)
        and _script_referenced_in_workflow(
            script_path=path,
            workflow_text=workflow_text,
        )
    }


def _unreferenced_workflow_script_warnings(
    *,
    file_paths: list[str],
    per_file_diffs: dict[str, str],
    post_image_files: dict[str, str],
) -> list[str]:
    """Warn when changed scripts are not referenced by any changed workflow."""
    workflows = [
        path
        for path in file_paths
        if path_matches_any_glob(path=path, patterns=(".github/workflows/**",))
    ]
    if not workflows:
        return []

    referenced: set[str] = set()
    for workflow in workflows:
        referenced.update(
            _referenced_scripts_in_workflow(
                workflow_path=workflow,
                workflow_diff=per_file_diffs.get(workflow, ""),
                post_image_files=post_image_files,
                file_paths=file_paths,
            ),
        )
    warnings: list[str] = []
    for path in file_paths:
        if _is_workflow_linked_script(path=path) and path not in referenced:
            warnings.append(
                f"Script {path} changed alongside workflows but is not referenced "
                "in any changed workflow diff; grouped separately.",
            )
    return warnings


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
            and matches_test_for_source(
                test_path=candidate,
                source_stem=stem,
                source_path=path,
            )
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
    classification_map: dict[str, FileClassification],
) -> tuple[list[str], dict[str, str], list[str], list[str]]:
    """Sample repetitive identical diffs, keeping three representative files."""
    signature_groups: dict[str, list[str]] = defaultdict(list)
    for path in file_paths:
        signature = _hunk_signature(
            diff_text=per_file_diffs.get(path, ""),
            path=path,
        )
        signature_groups[signature].append(path)

    sampled_paths: list[str] = []
    sampling_notes: dict[str, str] = {}
    sampling_warnings: list[str] = []
    skipped_paths: list[str] = []

    for paths in signature_groups.values():
        if len(paths) <= _REPETITIVE_FILE_THRESHOLD:
            sampled_paths.extend(paths)
            continue

        selected = _sort_files_by_priority(
            files=paths,
            classification_map=classification_map,
        )[:_REPETITIVE_SAMPLE_COUNT]
        sampled_paths.extend(selected)
        omitted = sorted(set(paths) - set(selected))
        skipped_paths.extend(omitted)
        omitted_preview = ", ".join(omitted[:10])
        if len(omitted) > 10:
            omitted_preview = f"{omitted_preview}, and {len(omitted) - 10} more"
        note = (
            f"{len(paths)} files share identical diff hunks; "
            f"sampled {_REPETITIVE_SAMPLE_COUNT} representative files "
            f"({omitted_preview} omitted)."
        )
        sampling_warnings.append(note)
        for path in selected:
            sampling_notes[path] = note

    return sorted(set(sampled_paths)), sampling_notes, skipped_paths, sampling_warnings


def _lookup_sampling_note(
    *,
    group_files: list[str],
    sampling_notes: dict[str, str],
) -> str | None:
    """Return a sampling note when a chunk contains sampled representative files."""
    matched_notes = [
        sampling_notes[path] for path in group_files if path in sampling_notes
    ]
    if not matched_notes:
        return None
    return " ".join(dict.fromkeys(matched_notes))


def _split_group_to_budget(
    *,
    group_files: list[str],
    per_file_diffs: dict[str, str],
    max_tokens: int,
    classification_map: dict[str, FileClassification],
) -> list[list[str]]:
    """Split a semantic group into multiple file lists that fit the token budget."""
    ordered_files = _sort_files_by_priority(
        files=group_files,
        classification_map=classification_map,
    )
    combined_diff = _combine_diffs(
        files=ordered_files,
        per_file_diffs=per_file_diffs,
    )
    if estimate_tokens(combined_diff) <= max_tokens:
        return [ordered_files]

    if len(ordered_files) == 1:
        return [ordered_files]

    midpoint = max(1, len(ordered_files) // 2)
    left = ordered_files[:midpoint]
    right = ordered_files[midpoint:]
    return _split_group_to_budget(
        group_files=left,
        per_file_diffs=per_file_diffs,
        max_tokens=max_tokens,
        classification_map=classification_map,
    ) + _split_group_to_budget(
        group_files=right,
        per_file_diffs=per_file_diffs,
        max_tokens=max_tokens,
        classification_map=classification_map,
    )


def _build_group_chunks(
    *,
    files: list[str],
    per_file_diffs: dict[str, str],
    max_tokens: int,
    classification_map: dict[str, FileClassification],
    relationship: RelationshipLabel,
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

        solo = _combine_diffs(files=[path], per_file_diffs=per_file_diffs)
        if estimate_tokens(solo) <= max_tokens:
            included = [path]
            included_diff = solo
            continue

        truncated_diff, was_truncated = truncate_to_budget(
            text=per_file_diffs[path],
            max_tokens=max_tokens,
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
    if (
        FileDomain.SHELL in domains
        and FileDomain.DOCS in domains
        and FileDomain.SOURCE not in domains
    ):
        return 0
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


def _hunk_signature(*, diff_text: str, path: str) -> str:
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
    parent = PurePosixPath(path).parent.as_posix()
    payload = f"{parent}\n{normalized}"
    return hashlib.sha256(
        payload.encode("utf-8", errors="surrogateescape"),
    ).hexdigest()


def _is_test_for_any(*, path: str, sources: list[str]) -> bool:
    """Return True when ``path`` is a test for any source in ``sources``."""
    return any(
        matches_test_for_source(
            test_path=path,
            source_stem=PurePosixPath(source).stem,
            source_path=source,
        )
        for source in sources
        if not is_test_path(source)
    )
