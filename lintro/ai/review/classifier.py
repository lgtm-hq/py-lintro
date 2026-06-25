"""Changed-file domain classification for AI review."""

from __future__ import annotations

from pathlib import PurePosixPath

from lintro.ai.review.enums.file_domain import FileDomain
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.file_classification import FileClassification
from lintro.ai.review.path_utils import is_test_path

_DOMAIN_GLOBS: dict[FileDomain, tuple[str, ...]] = {
    FileDomain.SHELL: ("**/*.sh", "**/*.bash"),
    FileDomain.CI: (".github/workflows/**", ".github/actions/**"),
    FileDomain.PYTHON: ("**/*.py",),
    FileDomain.RUST: ("**/*.rs",),
    FileDomain.TYPESCRIPT: ("**/*.ts", "**/*.tsx", "**/*.js", "**/*.jsx"),
    FileDomain.DOCS: ("docs/**", "**/*.md"),
    FileDomain.API: (
        "**/openapi/**",
        "**/routes/**",
        "**/api/**",
        "**/schemas/**",
    ),
}

_SECURITY_KEYWORDS: tuple[str, ...] = ("auth", "security")
_SECURITY_SOURCE_DOMAINS: frozenset[FileDomain] = frozenset(
    {
        FileDomain.CI,
        FileDomain.SHELL,
        FileDomain.PYTHON,
        FileDomain.RUST,
        FileDomain.TYPESCRIPT,
    },
)


def classify_changed_files(files: list[ChangedFile]) -> list[FileClassification]:
    """Classify changed files into one or more review domains.

    Args:
        files: Changed files from the review diff context.

    Returns:
        Domain classifications for each changed file.
    """
    return [
        FileClassification(
            path=changed_file.path,
            domains=_classify_path(path=changed_file.path),
        )
        for changed_file in files
    ]


def _classify_path(*, path: str) -> list[str]:
    """Return domain labels for a single path.

    Args:
        path: Repository-relative file path.

    Returns:
        Matching domain labels for the path.
    """
    normalized = path.replace("\\", "/")
    pure_path = PurePosixPath(normalized)
    matched_domains: list[FileDomain] = []

    for domain, patterns in _DOMAIN_GLOBS.items():
        if domain == FileDomain.SHELL and normalized.startswith("scripts/"):
            matched_domains.append(domain)
            continue
        if any(pure_path.match(pattern) for pattern in patterns):
            matched_domains.append(domain)

    if is_test_path(normalized):
        matched_domains.append(FileDomain.TEST)

    if _matches_security(path=normalized, matched_domains=matched_domains):
        matched_domains.append(FileDomain.SECURITY)

    return [domain.value for domain in matched_domains]


def _matches_security(
    *,
    path: str,
    matched_domains: list[FileDomain],
) -> bool:
    """Return True when a source file should receive the security domain tag.

    Args:
        path: Repository-relative file path.
        matched_domains: Domains already matched for the path.

    Returns:
        True when the security domain should be added.
    """
    if not _SECURITY_SOURCE_DOMAINS.intersection(matched_domains):
        return False

    path_lower = path.lower()
    if any(keyword in path_lower for keyword in _SECURITY_KEYWORDS):
        return True

    return path.startswith("scripts/")
