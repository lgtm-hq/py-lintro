"""Changed-file domain classification for AI review."""

from __future__ import annotations

from pathlib import PurePosixPath

from lintro.ai.review.enums.file_domain import FileDomain
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.file_classification import FileClassification
from lintro.ai.review.path_utils import is_test_path

_DOMAIN_GLOBS: dict[FileDomain, tuple[str, ...]] = {
    FileDomain.SHELL: ("**/*.sh", "**/*.bash", "**/*.bats"),
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


def _classify_path(*, path: str) -> list[FileDomain]:
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
        if _matches_domain_pattern(pure_path=pure_path, patterns=patterns):
            matched_domains.append(domain)

    if is_test_path(normalized):
        matched_domains.append(FileDomain.TEST)

    if _matches_security(path=normalized, matched_domains=matched_domains):
        matched_domains.append(FileDomain.SECURITY)

    return matched_domains


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
    segments = {part.lower() for part in PurePosixPath(path).parts}
    return any(keyword in segments for keyword in _SECURITY_KEYWORDS) or any(
        f"/{keyword}/" in f"/{path_lower}/" for keyword in _SECURITY_KEYWORDS
    )


def _matches_domain_pattern(
    *,
    pure_path: PurePosixPath,
    patterns: tuple[str, ...],
) -> bool:
    """Return True when a path matches any domain glob pattern.

    Supports recursive ``**`` segments for nested repository paths.
    """
    path_str = pure_path.as_posix()
    for pattern in patterns:
        if _path_matches_glob(path=path_str, pattern=pattern):
            return True
    return False


def _path_matches_glob(*, path: str, pattern: str) -> bool:
    """Match a repository path against a glob pattern."""
    if "**" not in pattern:
        return PurePosixPath(path).match(pattern)

    if pattern.startswith("**/") and pattern.endswith("/**") and len(pattern) > 6:
        middle = pattern[3:-3]
        return f"/{middle}/" in f"/{path}/"

    if pattern.startswith("**/") and pattern[3:].startswith("*."):
        return PurePosixPath(path).match(pattern[3:])

    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        return path == prefix or path.startswith(f"{prefix}/")

    return PurePosixPath(path).match(pattern)
