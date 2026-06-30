"""Changed-file domain classification for AI review.

Domains are review lenses for grouping and prioritizing chunks, not an exhaustive
file taxonomy. Prefer broad path and suffix heuristics plus a ``SOURCE`` fallback
over one-off filename or language registries. Extend classification only when
misclassification would change review behavior (for example docs tagged as source).
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from lintro.ai.review.enums.file_domain import FileDomain
from lintro.ai.review.glob_utils import path_matches_any_glob
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.file_classification import FileClassification
from lintro.ai.review.path_utils import is_test_path

_DOMAIN_GLOBS: dict[FileDomain, tuple[str, ...]] = {
    FileDomain.SHELL: (
        "**/*.{sh,bash,bats}",
        "scripts/**",
        "bin/**",
    ),
    FileDomain.CI: (
        ".github/workflows/**",
        ".github/actions/**",
        ".github/*.yml",
        ".github/*.yaml",
    ),
    FileDomain.DOCS: (
        "docs/**",
        "**/*.md",
        "**/*.rst",
    ),
}

_API_PATH_SEGMENTS: frozenset[str] = frozenset(
    {"api", "routes", "openapi", "schemas"},
)
_SECURITY_KEYWORDS: tuple[str, ...] = ("auth", "security")
_DOC_FILE_STEMS: frozenset[str] = frozenset(
    {
        "changelog",
        "readme",
        "license",
        "authors",
        "contributing",
        "codeowners",
        "notice",
    },
)
_SECURITY_SOURCE_DOMAINS: frozenset[FileDomain] = frozenset(
    {
        FileDomain.CI,
        FileDomain.CONFIG,
        FileDomain.SHELL,
        FileDomain.SOURCE,
    },
)

_NON_SOURCE_DOMAINS: frozenset[FileDomain] = frozenset(
    {
        FileDomain.CI,
        FileDomain.DOCS,
        FileDomain.DEPS,
        FileDomain.CONFIG,
    },
)

_CONFIG_SUFFIXES: frozenset[str] = frozenset(
    {
        ".toml",
        ".cfg",
        ".ini",
        ".json",
        ".yaml",
        ".yml",
    },
)

_DOC_SUFFIXES: frozenset[str] = frozenset({".md", ".rst"})
_SHELL_SCRIPT_SUFFIXES: frozenset[str] = frozenset({".sh", ".bash", ".bats"})
_DEPENDENCY_TEXT_PREFIXES: tuple[str, ...] = ("requirements", "constraints")
_DEPENDENCY_MANIFEST_NAMES: frozenset[str] = frozenset(
    {
        "bun.lock",
        "bun.lockb",
        "cargo.toml",
        "composer.json",
        "go.mod",
        "npm-shrinkwrap.json",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "pyproject.toml",
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
    matched_domains: list[FileDomain] = []

    for domain, patterns in _DOMAIN_GLOBS.items():
        if path_matches_any_glob(path=normalized, patterns=patterns):
            matched_domains.append(domain)

    pure_path = PurePosixPath(normalized)
    if _matches_api(path=normalized) and FileDomain.API not in matched_domains:
        matched_domains.append(FileDomain.API)
    if _matches_deps(path=normalized) and FileDomain.DEPS not in matched_domains:
        matched_domains.append(FileDomain.DEPS)
    if _matches_config(path=normalized) and FileDomain.CONFIG not in matched_domains:
        matched_domains.append(FileDomain.CONFIG)

    if (
        pure_path.suffix == ""
        and pure_path.stem.lower() in _DOC_FILE_STEMS
        and FileDomain.DOCS not in matched_domains
    ):
        matched_domains.append(FileDomain.DOCS)

    if is_test_path(normalized):
        matched_domains.append(FileDomain.TEST)

    if _should_tag_source(
        path=normalized,
        matched_domains=matched_domains,
    ):
        matched_domains.append(FileDomain.SOURCE)

    if not matched_domains:
        matched_domains.append(_fallback_domain(path=normalized))

    if _matches_security(path=normalized, matched_domains=matched_domains):
        matched_domains.append(FileDomain.SECURITY)

    return matched_domains


def _matches_api(*, path: str) -> bool:
    """Return True when a path sits under a conventional API directory segment."""
    parts = {part.lower() for part in PurePosixPath(path).parts[:-1]}
    return bool(parts.intersection(_API_PATH_SEGMENTS))


def _matches_deps(*, path: str) -> bool:
    """Return True when a path looks like a dependency lock or manifest."""
    pure_path = PurePosixPath(path)
    name = pure_path.name.lower()
    if name in _DEPENDENCY_MANIFEST_NAMES:
        return True
    if name == "go.sum" or name.endswith(".lock"):
        return True
    return pure_path.suffix.lower() == ".txt" and name.startswith(
        _DEPENDENCY_TEXT_PREFIXES,
    )


def _matches_config(*, path: str) -> bool:
    """Return True when a path looks like project configuration by suffix."""
    normalized = path.replace("\\", "/")
    if normalized.startswith(".github/"):
        return False
    return PurePosixPath(normalized).suffix.lower() in _CONFIG_SUFFIXES


def _fallback_domain(*, path: str) -> FileDomain:
    """Return a conservative non-empty domain for otherwise unmatched paths."""
    pure_path = PurePosixPath(path)
    suffix = pure_path.suffix.lower()
    stem = pure_path.stem.lower()
    if suffix in _CONFIG_SUFFIXES:
        return FileDomain.CONFIG
    if suffix in {".md", ".rst"}:
        return FileDomain.DOCS
    if suffix == "" and stem in _DOC_FILE_STEMS:
        return FileDomain.DOCS
    return FileDomain.SOURCE


def _should_tag_source(
    *,
    path: str,
    matched_domains: list[FileDomain],
) -> bool:
    """Return True when a path should receive the SOURCE domain tag."""
    if is_test_path(path):
        return False

    domains = set(matched_domains)
    if domains and domains <= _NON_SOURCE_DOMAINS:
        return False

    pure_path = PurePosixPath(path)
    suffix = pure_path.suffix.lower()

    if domains == {FileDomain.SHELL, FileDomain.DOCS}:
        return False
    if FileDomain.SHELL in domains:
        non_shell_domains = domains - {FileDomain.SHELL}
        if non_shell_domains and non_shell_domains <= _NON_SOURCE_DOMAINS:
            return False
    if domains == {FileDomain.SHELL}:
        if suffix in _SHELL_SCRIPT_SUFFIXES:
            return True
        if suffix == ".txt":
            return False
        return (
            bool(suffix)
            and suffix not in _CONFIG_SUFFIXES
            and suffix not in _DOC_SUFFIXES
        )
    if domains == {FileDomain.API, FileDomain.DOCS}:
        return False
    return bool(domains - _NON_SOURCE_DOMAINS)


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

    pure_path = PurePosixPath(path)
    if (
        FileDomain.DOCS in matched_domains
        and FileDomain.SOURCE not in matched_domains
        and (
            pure_path.suffix.lower() in {".md", ".txt", ".rst"}
            or pure_path.suffix == ""
        )
    ):
        return False

    path_lower = path.lower()
    segments = {part.lower() for part in pure_path.parts}
    stem_lower = pure_path.stem.lower()

    if pure_path.suffix == "" and stem_lower in _DOC_FILE_STEMS:
        return False

    segment_tokens = {
        token
        for part in pure_path.parts[:-1]
        for token in re.split(r"[-_.]", part.lower())
        if token
    }
    if any(token in _SECURITY_KEYWORDS for token in segment_tokens):
        return True
    if any(keyword in segments for keyword in _SECURITY_KEYWORDS):
        return True
    stem_tokens = re.split(r"[-_.]", stem_lower)
    if any(token in _SECURITY_KEYWORDS for token in stem_tokens):
        return True
    if stem_lower in _SECURITY_KEYWORDS:
        return True
    return any(f"/{keyword}/" in f"/{path_lower}/" for keyword in _SECURITY_KEYWORDS)
