"""Interaction path generation for AI diff review prompts."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable

from lintro.ai.review.enums.file_domain import FileDomain
from lintro.ai.review.models.file_classification import FileClassification

__all__ = ["generate_interaction_paths"]

_PATH_BUILDERS: list[tuple[frozenset[str], Callable[[list[str]], str]]] = [
    (
        frozenset({FileDomain.CI.value, FileDomain.SHELL.value}),
        lambda files: (
            "**Path A — CI + shell:** Trace workflow inputs → env vars → "
            f"sourced script behavior → exit codes. Changed: {', '.join(files[:5])}"
        ),
    ),
    (
        frozenset({FileDomain.SHELL.value}),
        lambda files: (
            "**Path A — Shell exit semantics:** Trace exit codes for ALL branches "
            "where error/removal ID vars are non-empty. "
            f"Changed: {', '.join(files[:5])}"
        ),
    ),
    (
        frozenset({FileDomain.CI.value, FileDomain.DOCS.value}),
        lambda files: (
            "**Path B — CI docs vs presets:** Cross-check workflow docs/presets vs "
            f"script defaults. Changed: {', '.join(files[:5])}"
        ),
    ),
    (
        frozenset({FileDomain.TEST.value}),
        lambda files: (
            "**Path C — Test vs production defaults:** Compare test setup/fixture "
            f"defaults vs production/workflow defaults. Changed: {', '.join(files[:5])}"
        ),
    ),
    (
        frozenset({FileDomain.DOCS.value}),
        lambda files: (
            "**Path D — Docs vs code:** Cross-check documented behavior vs "
            f"implementation. Changed: {', '.join(files[:5])}"
        ),
    ),
    (
        frozenset({FileDomain.SECURITY.value}),
        lambda files: (
            "**Path E — Security exit semantics:** Trace security-sensitive branches "
            f"to exit 0 vs exit 1 / HTTP 403 vs 200. Changed: {', '.join(files[:5])}"
        ),
    ),
    (
        frozenset({FileDomain.RUST.value, FileDomain.TYPESCRIPT.value}),
        lambda files: (
            "**Path F — Server ↔ client:** Trace server route → middleware → DB → "
            f"client API parse → UI component. Changed: {', '.join(files[:5])}"
        ),
    ),
    (
        frozenset({FileDomain.RUST.value, FileDomain.API.value}),
        lambda files: (
            "**Path G — OpenAPI vs routes:** Cross-check OpenAPI registration vs "
            f"route handlers vs error response shapes. Changed: {', '.join(files[:5])}"
        ),
    ),
    (
        frozenset({FileDomain.TYPESCRIPT.value, FileDomain.API.value}),
        lambda files: (
            "**Path H — Auth/API → UI:** Trace auth/API payload parsing → UI "
            f"copy/state; runtime validation vs casts. Changed: {', '.join(files[:5])}"
        ),
    ),
    (
        frozenset({FileDomain.PYTHON.value, FileDomain.TEST.value}),
        lambda files: (
            "**Path I — Pytest vs production:** Compare pytest fixtures/defaults vs "
            f"production config defaults. Changed: {', '.join(files[:5])}"
        ),
    ),
    (
        frozenset({FileDomain.PYTHON.value, FileDomain.SECURITY.value}),
        lambda files: (
            "**Path J — Auth guards:** Trace auth decorators/guards on all new/"
            f"changed routes. Changed: {', '.join(files[:5])}"
        ),
    ),
]

_MAX_PATHS = 7
_REPETITIVE_THRESHOLD = 34


def generate_interaction_paths(
    *,
    classifications: list[FileClassification],
    changed_files: list[str],
) -> str:
    """Generate domain-triggered interaction paths for the review prompt.

    Selects up to seven path templates matching detected file domains and
    emits explicit trace instructions referencing changed file names.

    Args:
        classifications: Domain classifications for changed files.
        changed_files: Repository-relative changed file paths.

    Returns:
        Formatted interaction path block for prompt injection.
    """
    if not changed_files:
        return "No interaction paths — no changed files."

    domain_set = _collect_domains(classifications=classifications)
    paths: list[str] = []
    used_labels: set[str] = set()

    for required_domains, builder in _PATH_BUILDERS:
        if not required_domains.issubset(domain_set):
            continue
        matching_files = _files_for_domains(
            classifications=classifications,
            domains=required_domains,
            changed_files=changed_files,
        )
        if not matching_files:
            matching_files = changed_files
        path_text = builder(matching_files)
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


def _collect_domains(*, classifications: list[FileClassification]) -> set[str]:
    """Collect unique domain labels from classifications."""
    domains: set[str] = set()
    for classification in classifications:
        domains.update(classification.domains)
    return domains


def _files_for_domains(
    *,
    classifications: list[FileClassification],
    domains: frozenset[str],
    changed_files: list[str],
) -> list[str]:
    """Return changed files whose classification includes any required domain."""
    classification_map = {item.path: item for item in classifications}
    matched: list[str] = []
    for path in changed_files:
        classification = classification_map.get(path)
        if classification is None:
            continue
        if domains.intersection(classification.domains):
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
        if domain not in {FileDomain.TEST.value, FileDomain.DOCS.value}
    )
    if not domain_counts:
        return None

    dominant_domain = domain_counts.most_common(1)[0][0]
    domain_files = [
        path
        for path in changed_files
        if any(
            dominant_domain in classification.domains
            for classification in classifications
            if classification.path == path
        )
    ]
    sample = domain_files[:3] if domain_files else changed_files[:3]
    sample_text = ", ".join(f"`{path}`" for path in sample)
    return (
        f"**Path — Bulk repetitive changes:** {len(changed_files)} files with "
        f"similar {dominant_domain} patterns — sample {sample_text} and verify "
        "the pattern holds across all instances."
    )
