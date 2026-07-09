"""Interaction path generation for AI diff review prompts."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from lintro.ai.review.enums.file_domain import FileDomain
from lintro.ai.review.file_language import languages_for_path
from lintro.ai.review.models.file_classification import FileClassification

__all__ = ["generate_interaction_paths"]

_MAX_PATHS = 7
_REPETITIVE_THRESHOLD = 34


@dataclass(frozen=True)
class _PathSpec:
    """Domain and optional language requirements for an interaction path."""

    required_domains: frozenset[FileDomain]
    required_languages: frozenset[str]
    builder: Callable[[list[str]], str]
    skip_when_domains: frozenset[FileDomain] = frozenset()


_PATH_BUILDERS: list[_PathSpec] = [
    _PathSpec(
        required_domains=frozenset({FileDomain.CI, FileDomain.SHELL}),
        required_languages=frozenset(),
        builder=lambda files: (
            "**Path A — CI + shell:** Trace workflow inputs → env vars → "
            f"sourced script behavior → exit codes. Changed: {', '.join(files[:5])}"
        ),
    ),
    _PathSpec(
        required_domains=frozenset({FileDomain.SHELL}),
        required_languages=frozenset(),
        skip_when_domains=frozenset({FileDomain.CI}),
        builder=lambda files: (
            "**Path A — Shell exit semantics:** Trace exit codes for ALL branches "
            "where error/removal ID vars are non-empty. "
            f"Changed: {', '.join(files[:5])}"
        ),
    ),
    _PathSpec(
        required_domains=frozenset({FileDomain.CI, FileDomain.DOCS}),
        required_languages=frozenset(),
        builder=lambda files: (
            "**Path B — CI docs vs presets:** Cross-check workflow docs/presets vs "
            f"script defaults. Changed: {', '.join(files[:5])}"
        ),
    ),
    _PathSpec(
        required_domains=frozenset({FileDomain.TEST}),
        required_languages=frozenset(),
        builder=lambda files: (
            "**Path C — Test vs production defaults:** Compare test setup/fixture "
            f"defaults vs production/workflow defaults. Changed: {', '.join(files[:5])}"
        ),
    ),
    _PathSpec(
        required_domains=frozenset({FileDomain.DOCS}),
        required_languages=frozenset(),
        builder=lambda files: (
            "**Path D — Docs vs code:** Cross-check documented behavior vs "
            f"implementation. Changed: {', '.join(files[:5])}"
        ),
    ),
    _PathSpec(
        required_domains=frozenset({FileDomain.SECURITY}),
        required_languages=frozenset(),
        builder=lambda files: (
            "**Path E — Security exit semantics:** Trace security-sensitive branches "
            f"to exit 0 vs exit 1 / HTTP 403 vs 200. Changed: {', '.join(files[:5])}"
        ),
    ),
    _PathSpec(
        required_domains=frozenset({FileDomain.SOURCE}),
        required_languages=frozenset({"rust", "ts"}),
        builder=lambda files: (
            "**Path F — Server ↔ client:** Trace server route → middleware → DB → "
            f"client API parse → UI component. Changed: {', '.join(files[:5])}"
        ),
    ),
    _PathSpec(
        required_domains=frozenset({FileDomain.SOURCE, FileDomain.API}),
        required_languages=frozenset({"rust"}),
        builder=lambda files: (
            "**Path G — OpenAPI vs routes:** Cross-check OpenAPI registration vs "
            f"route handlers vs error response shapes. Changed: {', '.join(files[:5])}"
        ),
    ),
    _PathSpec(
        required_domains=frozenset({FileDomain.SOURCE, FileDomain.API}),
        required_languages=frozenset({"ts"}),
        builder=lambda files: (
            "**Path H — Auth/API → UI:** Trace auth/API payload parsing → UI "
            f"copy/state; runtime validation vs casts. Changed: {', '.join(files[:5])}"
        ),
    ),
    _PathSpec(
        required_domains=frozenset({FileDomain.SOURCE, FileDomain.TEST}),
        required_languages=frozenset(),
        builder=lambda files: (
            "**Path I — Pytest vs production:** Compare pytest fixtures/defaults vs "
            f"production config defaults. Changed: {', '.join(files[:5])}"
        ),
    ),
    _PathSpec(
        required_domains=frozenset({FileDomain.SOURCE, FileDomain.SECURITY}),
        required_languages=frozenset(),
        builder=lambda files: (
            "**Path J — Auth guards:** Trace auth decorators/guards on all new/"
            f"changed routes. Changed: {', '.join(files[:5])}"
        ),
    ),
]


def generate_interaction_paths(
    *,
    classifications: list[FileClassification],
    changed_files: list[str],
    repo_root: Path | str | None = None,
) -> str:
    """Generate domain-triggered interaction paths for the review prompt.

    Selects up to seven path templates matching detected file domains and
    emits explicit trace instructions referencing changed file names.

    Args:
        classifications: Domain classifications for changed files.
        changed_files: Repository-relative changed file paths.
        repo_root: Optional repository root for extensionless script language tags.

    Returns:
        Formatted interaction path block for prompt injection.
    """
    if not changed_files:
        return "No interaction paths — no changed files."

    domain_set = _collect_domains(classifications=classifications)
    language_set = _collect_languages(
        classifications=classifications,
        repo_root=repo_root,
    )
    paths: list[str] = []
    used_labels: set[str] = set()

    for spec in _PATH_BUILDERS:
        if not _spec_matches(
            spec=spec,
            domain_set=domain_set,
            language_set=language_set,
        ):
            continue
        if spec.skip_when_domains.intersection(domain_set):
            continue
        matching_files = _files_for_spec(
            classifications=classifications,
            spec=spec,
            changed_files=changed_files,
            repo_root=repo_root,
        )
        if not matching_files:
            matching_files = changed_files
        path_text = spec.builder(matching_files)
        label = path_text.split(":", maxsplit=1)[0]
        if label in used_labels:
            continue
        used_labels.add(label)
        paths.append(path_text)
        if len(paths) >= _MAX_PATHS:
            break

    repetitive_note = _repetitive_bulk_path(
        changed_files=changed_files,
        classifications=classifications,
    )
    if repetitive_note and len(paths) < _MAX_PATHS:
        paths.append(repetitive_note)

    if not paths:
        sample = ", ".join(changed_files[:5])
        paths.append(
            f"**Path A — General integration:** Trace cross-file wiring for "
            f"changed files: {sample}",
        )

    return "\n\n".join(paths)


def _spec_matches(
    *,
    spec: _PathSpec,
    domain_set: set[FileDomain],
    language_set: set[str],
) -> bool:
    """Return True when a path spec matches the diff domains and languages."""
    if not spec.required_domains.issubset(domain_set):
        return False
    if not spec.required_languages:
        return True
    return spec.required_languages.issubset(language_set)


def _collect_domains(*, classifications: list[FileClassification]) -> set[FileDomain]:
    """Collect unique domain labels from classifications."""
    domains: set[FileDomain] = set()
    for classification in classifications:
        domains.update(classification.domains)
    return domains


def _collect_languages(
    *,
    classifications: list[FileClassification],
    repo_root: Path | str | None,
) -> set[str]:
    """Collect unique identify language tags from classified paths."""
    languages: set[str] = set()
    for classification in classifications:
        languages |= languages_for_path(
            path=classification.path,
            repo_root=repo_root,
        )
    return languages


def _files_for_spec(
    *,
    classifications: list[FileClassification],
    spec: _PathSpec,
    changed_files: list[str],
    repo_root: Path | str | None,
) -> list[str]:
    """Return changed files matching a path spec's domain and language axes."""
    classification_map = {item.path: item for item in classifications}
    matched: list[str] = []
    for path in changed_files:
        classification = classification_map.get(path)
        if classification is None:
            continue
        if not spec.required_domains.intersection(classification.domains):
            continue
        if spec.required_languages:
            path_languages = languages_for_path(path=path, repo_root=repo_root)
            if not spec.required_languages.intersection(path_languages):
                continue
        matched.append(path)
    return matched


def _repetitive_bulk_path(
    *,
    changed_files: list[str],
    classifications: list[FileClassification],
) -> str | None:
    """Emit a sampling path when many identical-pattern files changed."""
    if len(changed_files) < _REPETITIVE_THRESHOLD:
        return None

    domain_counts = Counter(
        domain
        for classification in classifications
        for domain in classification.domains
        if domain not in {FileDomain.TEST, FileDomain.DOCS}
    )
    if not domain_counts:
        return None

    dominant_domain = domain_counts.most_common(1)[0][0]
    classification_map = {item.path: item for item in classifications}
    domain_files = [
        path
        for path in changed_files
        if (classification := classification_map.get(path)) is not None
        and dominant_domain in classification.domains
    ]
    sample = domain_files[:3] if domain_files else changed_files[:3]
    sample_text = ", ".join(f"`{path}`" for path in sample)
    return (
        f"**Path — Bulk repetitive changes:** {len(changed_files)} files with "
        f"similar {dominant_domain.value} patterns — sample {sample_text} and verify "
        "the pattern holds across all instances."
    )
