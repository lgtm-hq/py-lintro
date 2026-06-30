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
_WORKFLOW_SCRIPT_PREFIXES = ("scripts/", "bin/", ".github/actions/")
_GITHUB_WORKSPACE_PREFIX = r"\$\{\{\s*github\.workspace\s*\}\}/"
_SHELL_FLAG = r"(?:\s+-[\w-]+(?:=\S+)?)*"
_WORKFLOW_SCRIPT_RUNTIMES = r"(?:bash|sh|python3?|node|bun)"
_RUN_INTERPRETER = rf"{_WORKFLOW_SCRIPT_RUNTIMES}{_SHELL_FLAG}\s+"
_UV_ARG_OPTIONS = frozenset({"with", "directory", "project", "package", "python"})
_UV_SHORT_OPERAND_OPTIONS = frozenset({"p", "w"})
_BUN_SUBCOMMANDS = frozenset({"run"})
_NODE_LONG_OPERAND_OPTIONS = frozenset({"import", "loader", "require"})
_BUN_LONG_OPERAND_OPTIONS = frozenset({"env-file", "preload", "require"})
_NODE_SHORT_OPERAND_OPTIONS = frozenset({"r"})
_RUNTIME_TERMINATING_LONG_OPTIONS = frozenset({"help", "version"})
_RUNTIME_TERMINATING_SHORT_OPTIONS = frozenset({"h", "v"})
_ENV_OPERAND_FLAGS = frozenset({"-u", "-U", "-C", "-S"})
_ENV_OPERAND_LONG_OPTIONS = frozenset({"chdir", "split-string"})
_SHELL_DISPATCH_WRAPPERS = frozenset({"exec", "command"})
_SHELL_COMPOUND_LEADERS = frozenset({"then", "do", "else", "elif"})
_UV_OPTION = (
    r"(?:"
    r"\s+--(?:with|directory|project|package)\s+\S+"
    r"|\s+--[\w-]+(?:=(?:[^\s\"']+|\"[^\"]*\"|'[^']*'))?"
    r")*"
)
_UV_RUN_WRAPPER = (
    rf"uv\s+run{_UV_OPTION}(?:\s+{_WORKFLOW_SCRIPT_RUNTIMES}){_SHELL_FLAG}\s+"
)
_RUN_COMMAND_PREFIX = rf"(?:{_RUN_INTERPRETER}|{_UV_RUN_WRAPPER}|uv\s+run\s+)"
_YAML_STEP_PREFIX = r"^[ \t]*(?:-\s+)?"
_RUN_STEP = rf"{_YAML_STEP_PREFIX}run:"
_USES_STEP = rf"{_YAML_STEP_PREFIX}uses:\s*"
_FAIL_FAST_OR_TAIL = re.compile(
    r"(?i)^(?:exit(?:\s+\d+)?|false|return(?:\s+\d+)?|:)\b",
)
_QUOTED_RUN_PATH = r'["\'](?:\./)?'
_NON_EXECUTION_COMMAND = re.compile(
    r"(?i)^(?:grep|cat|head|tail|less|more|sed|awk|sort|uniq|wc|find|ls|stat|"
    r"read|diff|cmp|strings|od|xxd|echo|printf|chmod|chown|chgrp|cp|mv|install|"
    r"ln|rsync|scp|touch|rm)\b",
)
_ASSIGNMENT_PREFIX = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_NON_EXECUTABLE_WORKFLOW_SUFFIXES = frozenset(
    {".cfg", ".ini", ".json", ".md", ".rst", ".toml", ".txt", ".yaml", ".yml"},
)
_ACTION_BUILD_ARTIFACT_DIRS = frozenset(
    {"coverage", "dist", "lib", "node_modules", "out", "vendor"},
)
_ACTION_SOURCE_LAYOUT_DIRS = frozenset({"source", "src"})


def _is_workflow_linked_script(*, path: str) -> bool:
    """Return True when a path may be invoked from a changed workflow."""
    if is_test_path(path):
        return False
    pure_path = PurePosixPath(path)
    suffix = pure_path.suffix.lower()
    if suffix in {".sh", ".bash"}:
        return True
    if path.startswith(".github/actions/") and len(pure_path.parts) >= 3:
        if suffix in _NON_EXECUTABLE_WORKFLOW_SUFFIXES and pure_path.name not in {
            "action.yml",
            "action.yaml",
        }:
            return False
        return pure_path.name in {
            "action.yml",
            "action.yaml",
            "Dockerfile",
            "run",
            "entrypoint",
        } or bool(suffix)
    return path.startswith(("scripts/", "bin/")) and (
        not suffix or suffix not in _NON_EXECUTABLE_WORKFLOW_SUFFIXES
    )


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


def _workflow_text_for_matching(
    *,
    workflow_path: str,
    workflow_diff: str,
    post_image_files: dict[str, str],
) -> str:
    """Return full post-change workflow text when available, else diff hunks."""
    if workflow_path in post_image_files:
        return post_image_files[workflow_path]
    return _workflow_post_image_text(workflow_diff=workflow_diff)


def _is_comment_line(*, line: str) -> bool:
    """Return True when a workflow or shell line is comment-only."""
    return line.lstrip().startswith("#")


def _normalize_posix_shell_path(*, path: str) -> str:
    """Normalize a repository-relative POSIX path."""
    normalized = path.replace("\\", "/").strip()
    if not normalized or normalized == ".":
        return ""
    parts: list[str] = []
    for part in PurePosixPath(normalized).parts:
        if part == "..":
            if parts and parts[-1] != "..":
                parts.pop()
            else:
                parts.append("..")
        elif part != ".":
            parts.append(part)
    return "/".join(parts)


def _shell_paths_equal(*, left: str, right: str) -> bool:
    """Return True when two repository-relative paths refer to the same file."""
    return _normalize_posix_shell_path(path=left) == _normalize_posix_shell_path(
        path=right,
    )


def _resolve_shell_path(*, token: str, cwd: str) -> str:
    """Resolve a shell token against the current working directory."""
    cleaned = token.strip().strip("\"'")
    if cleaned.startswith("./"):
        cleaned = cleaned[2:]
    base = _normalize_posix_shell_path(path=cwd)
    if base:
        return _normalize_posix_shell_path(path=f"{base}/{cleaned}")
    return _normalize_posix_shell_path(path=cleaned)


def _rest_after_first_shell_token(*, segment: str) -> str:
    """Return the remainder of a shell segment after its first token."""
    token_match = re.match(r'\s*"((?:\\.|[^"\\])*)"|\'([^\']*)\'|(\S+)', segment)
    if token_match is None:
        return segment
    return segment[token_match.end() :].lstrip()


def _strip_leading_shell_prefixes(*, segment: str) -> str:
    """Skip leading VAR=value assignments and ``env(1)`` wrappers."""
    remaining = segment.strip()
    while True:
        first_token = _first_shell_token(segment=remaining)
        if first_token is None:
            break
        if _ASSIGNMENT_PREFIX.match(first_token):
            remaining = _rest_after_first_shell_token(segment=remaining)
            continue
        if first_token.lower() == "env":
            remaining = _rest_after_first_shell_token(segment=remaining)
            while True:
                token = _first_shell_token(segment=remaining)
                if token is None:
                    break
                if _ASSIGNMENT_PREFIX.match(token):
                    remaining = _rest_after_first_shell_token(segment=remaining)
                    continue
                if token.startswith("-"):
                    remaining = _rest_after_first_shell_token(segment=remaining)
                    if token in _ENV_OPERAND_FLAGS:
                        operand = _first_shell_token(segment=remaining)
                        if operand is not None and not operand.startswith("-"):
                            remaining = _rest_after_first_shell_token(segment=remaining)
                    elif token.startswith("--"):
                        option_name = token[2:].split("=", 1)[0]
                        if (
                            option_name in _ENV_OPERAND_LONG_OPTIONS
                            and "=" not in token
                        ):
                            operand = _first_shell_token(segment=remaining)
                            if operand is not None and not operand.startswith("-"):
                                remaining = _rest_after_first_shell_token(
                                    segment=remaining,
                                )
                    continue
                break
            continue
        break
    return remaining


def _strip_uv_cli_options(*, segment: str) -> str:
    """Skip ``uv run`` CLI options that precede the invoked command."""
    remaining = segment.strip()
    while True:
        token = _first_shell_token(segment=remaining)
        if token is None:
            break
        if token.startswith("--"):
            option_name = token[2:].split("=", 1)[0]
            remaining = _rest_after_first_shell_token(segment=remaining)
            if "=" not in token and (
                option_name in _UV_ARG_OPTIONS or option_name.startswith("with")
            ):
                operand = _first_shell_token(segment=remaining)
                if operand is not None:
                    remaining = _rest_after_first_shell_token(segment=remaining)
            continue
        if len(token) >= 2 and token[0] == "-" and token[1] != "-":
            remaining = _rest_after_first_shell_token(segment=remaining)
            if "=" in token:
                continue
            option_body = token[1:]
            if option_body in _UV_SHORT_OPERAND_OPTIONS:
                operand = _first_shell_token(segment=remaining)
                if operand is not None:
                    remaining = _rest_after_first_shell_token(segment=remaining)
                continue
            if len(option_body) > 1 and option_body[-1] in _UV_SHORT_OPERAND_OPTIONS:
                operand = _first_shell_token(segment=remaining)
                if operand is not None:
                    remaining = _rest_after_first_shell_token(segment=remaining)
                continue
            continue
        break
    return remaining


def _runtime_long_option_needs_operand(*, runtime: str, option_name: str) -> bool:
    """Return True when a Node/Bun long option consumes a separate operand."""
    if runtime == "bun":
        return option_name in _BUN_LONG_OPERAND_OPTIONS
    if runtime.startswith("node"):
        return option_name in _NODE_LONG_OPERAND_OPTIONS
    return False


def _runtime_short_option_needs_operand(*, runtime: str, option_char: str) -> bool:
    """Return True when a Node/Bun short option consumes a separate operand."""
    return bool(runtime.startswith("node") and option_char == "r")


def _runtime_option_is_terminating(*, runtime: str, token: str) -> bool:
    """Return True when a Node/Bun flag ends execution before any script."""
    if not (runtime.startswith("node") or runtime == "bun"):
        return False
    if token.startswith("--"):
        option_name = token[2:].split("=", 1)[0]
        return option_name in _RUNTIME_TERMINATING_LONG_OPTIONS
    return bool(
        len(token) == 2
        and token[0] == "-"
        and token[1] in _RUNTIME_TERMINATING_SHORT_OPTIONS,
    )


def _clustered_runtime_option_needs_next_operand(*, runtime: str, token: str) -> bool:
    """Return True when a clustered short option still needs the next shell token."""
    option_body = token[1:]
    if not option_body or "=" in token:
        return False
    for index, char in enumerate(option_body):
        needs_operand = _runtime_short_option_needs_operand(
            runtime=runtime,
            option_char=char,
        )
        if runtime in {"bash", "sh"} and char == "o":
            needs_operand = True
        if not needs_operand:
            continue
        return index == len(option_body) - 1
    return False


def _strip_shell_interpreter_prefix(*, segment: str) -> str:
    """Skip a leading workflow script runtime and its flags."""
    remaining = segment.strip()
    runtime_match = re.match(
        rf"(?i)^(?P<runtime>{_WORKFLOW_SCRIPT_RUNTIMES})\b",
        remaining,
    )
    if runtime_match is None:
        return remaining
    runtime = runtime_match.group("runtime").lower()
    remaining = remaining[runtime_match.end() :].lstrip()
    if runtime == "bun":
        subcommand = _first_shell_token(segment=remaining)
        if subcommand is not None and subcommand.lower() in _BUN_SUBCOMMANDS:
            remaining = _rest_after_first_shell_token(segment=remaining)
    pending_operand = False
    while True:
        token = _first_shell_token(segment=remaining)
        if token is None:
            break
        if pending_operand:
            remaining = _rest_after_first_shell_token(segment=remaining)
            pending_operand = False
            continue
        if _runtime_option_is_terminating(runtime=runtime, token=token):
            return ""
        if re.match(r"(?i)^-c", token) or re.match(r"(?i)^-s", token):
            break
        if not token.startswith("-"):
            break
        if token.startswith("--"):
            option_name = token[2:].split("=", 1)[0]
            remaining = _rest_after_first_shell_token(segment=remaining)
            if "=" not in token and _runtime_long_option_needs_operand(
                runtime=runtime,
                option_name=option_name,
            ):
                pending_operand = True
            continue
        remaining = _rest_after_first_shell_token(segment=remaining)
        if "=" in token:
            continue
        option_body = token[1:]
        if len(option_body) == 1:
            if (
                _runtime_short_option_needs_operand(
                    runtime=runtime,
                    option_char=option_body,
                )
                or token in {"-W"}
                or re.match(r"(?i)^-o$", token)
            ):
                pending_operand = True
            continue
        if _clustered_runtime_option_needs_next_operand(runtime=runtime, token=token):
            pending_operand = True
            continue
    return remaining


def _strip_command_options(*, segment: str) -> str:
    """Skip ``command`` builtin option flags before the invoked executable."""
    remaining = segment.strip()
    while True:
        token = _first_shell_token(segment=remaining)
        if token is None:
            break
        if len(token) == 2 and token[0] == "-" and token[1] == "-":
            return _rest_after_first_shell_token(segment=remaining)
        if token.startswith("-"):
            remaining = _rest_after_first_shell_token(segment=remaining)
            continue
        break
    return remaining


def _strip_shell_dispatch_wrappers(*, segment: str) -> str:
    """Skip leading ``exec``/``command`` dispatch wrappers."""
    remaining = segment.strip()
    while True:
        first_token = _first_shell_token(segment=remaining)
        if first_token is None or first_token.lower() not in _SHELL_DISPATCH_WRAPPERS:
            break
        wrapper = first_token.lower()
        remaining = _rest_after_first_shell_token(segment=remaining)
        if wrapper == "command":
            remaining = _strip_command_options(segment=remaining)
    return remaining


def _strip_shell_compound_leaders(*, segment: str) -> str:
    """Skip compound-command leaders such as ``then`` and ``do``."""
    remaining = segment.strip()
    while True:
        first_token = _first_shell_token(segment=remaining)
        if first_token is None or first_token.lower() not in _SHELL_COMPOUND_LEADERS:
            break
        remaining = _rest_after_first_shell_token(segment=remaining)
    return remaining


def _strip_run_command_prefix(*, segment: str) -> str:
    """Skip interpreter and ``uv run`` wrappers before the invoked command."""
    remaining = segment.strip()
    if re.match(r"(?i)^uv\s+run\b", remaining):
        remaining = re.sub(r"(?i)^uv\s+run\b", "", remaining, count=1).lstrip()
        remaining = _strip_uv_cli_options(segment=remaining)
    return _strip_shell_interpreter_prefix(segment=remaining)


def _short_option_cluster_includes(*, token: str, option: str) -> bool:
    """Return True when a clustered bash/sh short-option token includes ``option``."""
    if not token.startswith("-") or token.startswith("--"):
        return False
    if re.fullmatch(rf"(?i)-{re.escape(option)}", token):
        return True
    body = token[1:]
    return bool(body) and body.isalpha() and option.lower() in body.lower()


def _trailing_shell_c_payload(*, segment: str) -> str | None:
    """Return a ``-c`` command-string payload after shell wrappers are stripped."""
    remaining = segment.strip()
    while True:
        token = _first_shell_token(segment=remaining)
        if token is None:
            return None
        if _short_option_cluster_includes(token=token, option="c"):
            remaining = _rest_after_first_shell_token(segment=remaining)
            return _first_shell_token(segment=remaining)
        if _short_option_cluster_includes(token=token, option="s"):
            return None
        if token.startswith("-"):
            remaining = _rest_after_first_shell_token(segment=remaining)
            continue
        return None


def _command_string_payload(*, segment: str) -> str | None:
    """Return the shell payload passed to ``bash``/``sh -c``, if present."""
    stripped = segment.strip()
    if not re.match(r"(?i)^(?:bash|sh)\b", stripped):
        return None
    remaining = re.sub(r"(?i)^(?:bash|sh)\b", "", stripped, count=1).lstrip()
    return _trailing_shell_c_payload(segment=remaining)


def _bash_stdin_invocation(*, segment: str) -> bool:
    """Return True when a segment invokes ``bash``/``sh -s`` (stdin script mode)."""
    stripped = segment.strip()
    if not re.match(r"(?i)^(?:bash|sh)\b", stripped):
        return False
    remaining = re.sub(r"(?i)^(?:bash|sh)\b", "", stripped, count=1).lstrip()
    while True:
        token = _first_shell_token(segment=remaining)
        if token is None:
            return False
        if _short_option_cluster_includes(token=token, option="s"):
            return True
        if token.startswith("-"):
            remaining = _rest_after_first_shell_token(segment=remaining)
            continue
        return False


def _shell_command_string_payload(*, segment: str) -> str | None:
    """Return ``bash``/``sh`` ``-c`` payloads that may execute a script path."""
    stripped = segment.strip()
    if not re.match(r"(?i)^(?:bash|sh)\b", stripped):
        return None
    return _command_string_payload(segment=stripped)


def _shell_command_string_payload_after_wrappers(*, segment: str) -> str | None:
    """Return ``bash``/``sh -c`` payload after stripping non-executing wrappers."""
    remaining = segment.strip()
    while remaining:
        payload = _shell_command_string_payload(segment=remaining)
        if payload is not None:
            return payload
        payload = _trailing_shell_c_payload(segment=remaining)
        if payload is not None:
            return payload
        previous = remaining
        remaining = _strip_leading_shell_prefixes(segment=remaining)
        payload = _shell_command_string_payload(segment=remaining)
        if payload is not None:
            return payload
        payload = _trailing_shell_c_payload(segment=remaining)
        if payload is not None:
            return payload
        remaining = _strip_run_command_prefix(segment=remaining)
        payload = _trailing_shell_c_payload(segment=remaining)
        if payload is not None:
            return payload
        remaining = _strip_shell_dispatch_wrappers(segment=remaining)
        remaining = _strip_shell_compound_leaders(segment=remaining)
        if remaining == previous:
            break
    return None


def _first_shell_token(*, segment: str) -> str | None:
    """Return the first token from a shell segment."""
    token_match = re.match(r'\s*"((?:\\.|[^"\\])*)"|\'([^\']*)\'|(\S+)', segment)
    if token_match is None:
        return None
    if token_match.group(1) is not None:
        return token_match.group(1)
    if token_match.group(2) is not None:
        return token_match.group(2)
    return token_match.group(3)


def _strip_workspace_prefix(*, token: str) -> str:
    """Remove a leading ``${{ github.workspace }}/`` prefix from a shell token."""
    cleaned = token.strip().strip("\"'")
    match = re.match(rf"(?i){_GITHUB_WORKSPACE_PREFIX}(.*)", cleaned)
    if match is None:
        return cleaned
    return match.group(1)


def _normalize_invoked_command_segment(*, segment: str) -> str:
    """Remove shell wrappers until the invoked command token is exposed."""
    remaining = segment.strip()
    while True:
        if _bash_stdin_invocation(segment=remaining):
            return remaining
        previous = remaining
        remaining = _strip_leading_shell_prefixes(segment=remaining)
        remaining = _strip_run_command_prefix(segment=remaining)
        remaining = _strip_shell_dispatch_wrappers(segment=remaining)
        remaining = _strip_shell_compound_leaders(segment=remaining)
        if remaining == previous:
            break
    return remaining


def _segment_invokes_path_directly(*, segment: str, path: str, cwd: str) -> bool:
    """Return True when ``path`` is the invoked command rather than an argument."""
    remaining = segment.strip()
    workspace_segment = re.match(
        rf"(?i)^{_GITHUB_WORKSPACE_PREFIX}(.+)$",
        remaining,
    )
    if workspace_segment is not None:
        remaining = workspace_segment.group(1).lstrip()

    remaining = _normalize_invoked_command_segment(segment=remaining)

    if _bash_stdin_invocation(segment=remaining):
        return False

    first_token = _first_shell_token(segment=remaining)
    if first_token is None:
        return False
    token_path = _strip_workspace_prefix(token=first_token)
    return _shell_paths_equal(
        left=_resolve_shell_path(token=token_path, cwd=cwd),
        right=path,
    )


def _segment_executes_reference_path(*, segment: str, path: str, cwd: str) -> bool:
    """Return True when one shell command segment executes ``path``."""
    stripped = segment.strip()
    if not stripped or _NON_EXECUTION_COMMAND.match(stripped):
        return False
    if _bash_stdin_invocation(segment=stripped):
        return False
    payload = _shell_command_string_payload_after_wrappers(segment=stripped)
    if payload is not None:
        return _line_references_path(line=payload, path=path, cwd=cwd)
    return _segment_invokes_path_directly(segment=stripped, path=path, cwd=cwd)


def _line_references_path(*, line: str, path: str, cwd: str) -> bool:
    """Return True when a shell line executes a script at ``path``."""
    stripped = line.strip()
    if not stripped or _is_comment_line(line=line):
        return False
    current = cwd
    for segment in re.split(r"&&|;", stripped):
        segment = segment.strip()
        if not segment:
            continue
        if _segment_executes_reference_path(
            segment=segment,
            path=path,
            cwd=current,
        ):
            return True
        if _segment_has_shell_pipeline(segment=segment):
            continue
        current = _shell_cwd_after_line(line=segment, cwd=current)
    return False


def _segment_has_shell_pipeline(*, segment: str) -> bool:
    """Return True when a segment contains a pipe operator other than ``||``."""
    return bool(re.search(r"(?<!\|)\|(?!\|)", segment))


def _shell_cwd_after_line(*, line: str, cwd: str) -> str:
    """Return cwd after sequential ``cd`` commands that persist in the shell."""
    current = _normalize_posix_shell_path(path=cwd)
    for segment in re.split(r"&&|;", line):
        segment = segment.strip()
        if not segment or _segment_has_shell_pipeline(segment=segment):
            continue
        if "||" in segment:
            left, right = (part.strip() for part in segment.split("||", 1))
            cd_match = re.match(r"(?i)^cd\s+(.+)$", left)
            if cd_match is None or not _FAIL_FAST_OR_TAIL.match(right):
                continue
            current = _resolve_shell_path(token=cd_match.group(1), cwd=current)
            continue
        cd_match = re.match(r"(?i)^cd\s+(.+)$", segment)
        if cd_match is None:
            continue
        current = _resolve_shell_path(token=cd_match.group(1), cwd=current)
    return current


def _single_run_command(*, line: str) -> re.Match[str] | None:
    """Return a match when a line contains a GitHub Actions ``run:`` step."""
    return re.search(rf"{_RUN_STEP}(?!\s*[|>])(?:\s+(.+))?$", line)


def _single_run_command_text(*, match: re.Match[str]) -> str | None:
    """Return the shell command text from a ``run:`` step match."""
    command = match.group(1)
    if command is None:
        return None
    return command.strip()


def _run_reference_patterns(*, path: str) -> tuple[re.Pattern[str], ...]:
    """Build ``run:`` matchers for supported repo-root script reference forms."""
    escaped = re.escape(path)
    boundary = r"(?:$|[^\w./-])"
    direct = (
        rf"{_RUN_STEP}\s*{escaped}{boundary}",
        rf"{_RUN_STEP}\s*\./{escaped}{boundary}",
        rf"{_RUN_STEP}\s*{_GITHUB_WORKSPACE_PREFIX}{escaped}{boundary}",
        rf"{_RUN_STEP}\s*{_QUOTED_RUN_PATH}{escaped}['\"]{boundary}",
        rf"{_RUN_STEP}\s*{_RUN_COMMAND_PREFIX}{escaped}{boundary}",
        rf"{_RUN_STEP}\s*{_RUN_COMMAND_PREFIX}\./{escaped}{boundary}",
        rf"{_RUN_STEP}\s*{_RUN_COMMAND_PREFIX}{_GITHUB_WORKSPACE_PREFIX}{escaped}{boundary}",
        rf"{_RUN_STEP}\s*{_RUN_COMMAND_PREFIX}{_QUOTED_RUN_PATH}{escaped}['\"]{boundary}",
    )
    return tuple(re.compile(pattern) for pattern in direct)


def _run_line_reference_surface(*, line: str) -> str:
    """Return a run line safe for regex matching (hide ``-c`` payload text)."""
    single_run = _single_run_command(line=line)
    if single_run is None:
        return line
    command = _single_run_command_text(match=single_run)
    if command is None:
        return line
    if _shell_command_string_payload_after_wrappers(segment=command) is not None:
        return line[: single_run.start(1)]
    return line


def _multiline_run_block_style(*, line: str) -> str | None:
    """Return ``literal`` or ``folded`` when a line opens a multiline ``run:`` block."""
    if _is_comment_line(line=line):
        return None
    if re.search(rf"{_YAML_STEP_PREFIX}run:\s*\|", line):
        return "literal"
    if re.search(rf"{_YAML_STEP_PREFIX}run:\s*>", line):
        return "folded"
    return None


def _line_starts_multiline_run_block(*, line: str) -> bool:
    """Return True when an uncommented line opens a multiline ``run:`` block."""
    return _multiline_run_block_style(line=line) is not None


def _runtime_command_executes_script(*, command: str) -> bool:
    """Return False when a runtime flag terminates before executing a script."""
    remaining = _strip_run_command_prefix(segment=command.strip())
    return bool(remaining.strip())


def _script_referenced_in_workflow(*, script_path: str, workflow_text: str) -> bool:
    """Return True when a workflow run/uses step references a script path."""
    action_dir = _github_action_directory(path=script_path)
    reference_paths = [script_path]
    if action_dir is not None:
        reference_paths.extend(_github_action_reference_paths(path=script_path))

    patterns: list[re.Pattern[str]] = []
    for path in reference_paths:
        patterns.extend(_run_reference_patterns(path=path))
        patterns.append(
            re.compile(
                rf"{_USES_STEP}\.?/?{re.escape(path)}(?:$|[^\w./-])",
            ),
        )
    for content in workflow_text.splitlines():
        if _is_comment_line(line=content):
            continue
        single_run = _single_run_command(line=content)
        command = (
            _single_run_command_text(match=single_run)
            if single_run is not None
            else None
        )
        if command is not None and _bash_stdin_invocation(segment=command):
            continue
        if command is not None and not _runtime_command_executes_script(
            command=command,
        ):
            continue
        if any(
            pattern.search(_run_line_reference_surface(line=content))
            for pattern in patterns
        ):
            return True
        if command is not None and any(
            _segment_executes_reference_path(
                segment=command,
                path=reference_path,
                cwd="",
            )
            or _line_references_path(line=command, path=reference_path, cwd="")
            for reference_path in reference_paths
        ):
            return True
    return _script_referenced_in_multiline_run_blocks(
        reference_paths=reference_paths,
        workflow_text=workflow_text,
    )


def _script_referenced_in_multiline_run_blocks(
    *,
    reference_paths: list[str],
    workflow_text: str,
) -> bool:
    """Match script paths inside multi-line ``run: |`` / ``run: >`` shell blocks."""
    run_indent: int | None = None
    block_style: str | None = None
    folded_lines: list[str] = []
    shell_cwd = ""

    def _folded_block_references_script() -> bool:
        if not folded_lines:
            return False
        joined = " ".join(part for part in folded_lines if part)
        return any(
            _line_references_path(line=joined, path=path, cwd=shell_cwd)
            for path in reference_paths
        )

    for line in workflow_text.splitlines():
        opener = _multiline_run_block_style(line=line)
        if opener is not None:
            if block_style == "folded" and _folded_block_references_script():
                return True
            run_indent = len(line) - len(line.lstrip(" "))
            block_style = opener
            folded_lines = []
            shell_cwd = ""
            continue
        if run_indent is None:
            continue
        if not line.strip():
            continue
        line_indent = len(line) - len(line.lstrip(" "))
        if line_indent <= run_indent:
            if block_style == "folded" and _folded_block_references_script():
                return True
            opener = _multiline_run_block_style(line=line)
            if opener is not None:
                run_indent = line_indent
                block_style = opener
                folded_lines = []
                shell_cwd = ""
                continue
            run_indent = None
            block_style = None
            folded_lines = []
            shell_cwd = ""
            continue
        if _is_comment_line(line=line):
            continue
        if block_style == "folded":
            folded_lines.append(line.strip())
            continue
        for path in reference_paths:
            if _line_references_path(line=line, path=path, cwd=shell_cwd):
                return True
        shell_cwd = _shell_cwd_after_line(line=line, cwd=shell_cwd)
    return block_style == "folded" and _folded_block_references_script()


def _workflow_post_image_text(*, workflow_diff: str) -> str:
    """Return the post-change workflow text represented by diff hunks."""
    lines: list[str] = []
    for line in workflow_diff.splitlines():
        if line.startswith(("+++", "---", "diff --git", "index ", "@@")):
            continue
        if line.startswith("+") or line.startswith(" "):
            lines.append(line[1:])
    return "\n".join(lines)


def _resolve_github_action_root(*, path: PurePosixPath) -> PurePosixPath | None:
    """Walk up from an action implementation file to the local action root."""
    parts = path.parts
    if len(parts) < 3 or parts[:2] != (".github", "actions"):
        return None
    if path.name in {"action.yml", "action.yaml"}:
        dir_path = path.parent
    elif path.suffix.lower() in _NON_EXECUTABLE_WORKFLOW_SUFFIXES:
        return None
    elif (
        path.name == "Dockerfile"
        or bool(path.suffix)
        or path.name in {"run", "entrypoint"}
    ):
        dir_path = path.parent
    else:
        return None
    dir_parts = dir_path.parts
    artifact_index = next(
        (
            index
            for index in range(3, len(dir_parts))
            if dir_parts[index] in _ACTION_BUILD_ARTIFACT_DIRS
        ),
        None,
    )
    if artifact_index is not None:
        return PurePosixPath(*dir_parts[:artifact_index])
    source_index = next(
        (
            index
            for index in range(3, len(dir_parts))
            if dir_parts[index] in _ACTION_SOURCE_LAYOUT_DIRS
        ),
        None,
    )
    if source_index is not None:
        return PurePosixPath(*dir_parts[:source_index])
    if len(dir_path.parts) >= 3 and dir_path.parts[:2] == (".github", "actions"):
        return dir_path
    return None


def _github_action_directory(*, path: str) -> str | None:
    """Return the local action directory represented by an action implementation."""
    action_root = _resolve_github_action_root(path=PurePosixPath(path))
    if action_root is None:
        return None
    return action_root.as_posix()


def _github_action_reference_paths(*, path: str) -> list[str]:
    """Return the local action directory for workflow ``uses:`` matching."""
    action_dir = _github_action_directory(path=path)
    if action_dir is None:
        return []
    return [action_dir]


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
