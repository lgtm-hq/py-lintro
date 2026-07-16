"""Shared path heuristics for AI diff review."""

from __future__ import annotations

from pathlib import PurePosixPath

from identify import identify

_TEST_NAME_MARKERS = (".spec.", ".test.", "_test.")
_TEST_LAYER_PARTS: frozenset[str] = frozenset({"unit", "integration"})
_E2E_DIR_EXACT: frozenset[str] = frozenset({"e2e", "playwright-tests"})
_ARTIFACT_DIR_PARTS: frozenset[str] = frozenset({"__snapshots__"})
# Generic wrappers that accompany nearly every identify result.
_GENERIC_IDENTIFY_TAGS: frozenset[str] = frozenset({"plain-text", "text"})
# Small denylist of identify tags for docs, config, data, and media. Extension
# coverage lives in identify's maintained map — not a local suffix table.
_NON_SOURCE_IDENTIFY_TAGS: frozenset[str] = frozenset(
    {
        "audio",
        "binary",
        "csv",
        "dotenv",
        "gif",
        "go-sum",
        "image",
        "ini",
        "jpeg",
        "json",
        "markdown",
        "png",
        "rst",
        "svg",
        "toml",
        "webp",
        "xml",
        "yaml",
    },
)


def _has_tests_ancestor(path: PurePosixPath) -> bool:
    return any(part in ("tests", "__tests__") for part in path.parts[:-1])


def _is_under_tests_directory(*, pure_path: PurePosixPath) -> bool:
    """Return True when a path sits under tests/ or __tests__."""
    return _has_tests_ancestor(pure_path) or pure_path.parts[:1] == ("tests",)


def _is_non_test_artifact(*, pure_path: PurePosixPath) -> bool:
    """Return True when a path under a test tree is docs, config, or fixture data."""
    suffix = pure_path.suffix.lower()
    name_lower = pure_path.name.lower()
    if suffix == "" and pure_path.stem.lower() == "readme":
        return True
    if name_lower.startswith(".env"):
        return True
    parent_parts = [part.lower() for part in pure_path.parts[:-1]]
    return any(part in _ARTIFACT_DIR_PARTS for part in parent_parts)


def _meaningful_source_identify_tags(*, name: str) -> set[str]:
    """Return identify language/source tags after stripping generic and data tags."""
    tags = set(identify.tags_from_filename(name))
    return tags - _GENERIC_IDENTIFY_TAGS - _NON_SOURCE_IDENTIFY_TAGS


def _looks_like_test_code(*, pure_path: PurePosixPath) -> bool:
    """Return True when a basename looks like executable test or helper code."""
    name = pure_path.name
    name_lower = name.lower()
    if name.endswith(".bats"):
        return True
    if name.startswith("test_") or any(marker in name for marker in _TEST_NAME_MARKERS):
        return True
    if _has_e2e_name_marker(name_lower=name_lower):
        return True
    return bool(_meaningful_source_identify_tags(name=name))


def _classify_path_under_test_tree(*, pure_path: PurePosixPath) -> bool | None:
    """Return whether a test-tree path is test code, or None if outside one."""
    under_e2e = _path_has_e2e_directory(pure_path=pure_path)
    under_tests = _is_under_tests_directory(pure_path=pure_path)
    if not (under_e2e or under_tests):
        return None
    if _is_non_test_artifact(pure_path=pure_path):
        return False
    return _looks_like_test_code(pure_path=pure_path)


def is_test_path(path: str) -> bool:
    """Return True when a path looks like a test file.

    Args:
        path: Repository-relative file path.

    Returns:
        True when the path matches common test layout patterns.
    """
    pure_path = PurePosixPath(path.replace("\\", "/"))
    name = pure_path.name
    if name.endswith(".bats"):
        return True
    test_tree_match = _classify_path_under_test_tree(pure_path=pure_path)
    if test_tree_match is not None:
        return test_tree_match
    if _has_e2e_name_marker(name_lower=name.lower()):
        return True
    return name.startswith("test_") or any(
        marker in name for marker in _TEST_NAME_MARKERS
    )


def is_e2e_test_path(path: str) -> bool:
    """Return True when a path looks like an end-to-end or browser test file.

    Args:
        path: Repository-relative file path.

    Returns:
        True when the path sits under a conventional E2E directory or uses an
        E2E-specific filename marker.
    """
    pure_path = PurePosixPath(path.replace("\\", "/"))
    e2e_directory_match = _is_test_file_in_e2e_directory(pure_path=pure_path)
    if e2e_directory_match is not None:
        return e2e_directory_match
    return _has_e2e_name_marker(name_lower=pure_path.name.lower())


def _path_has_e2e_directory(*, pure_path: PurePosixPath) -> bool:
    """Return True when a path sits under a recognized E2E directory segment."""
    parent_parts = [part.lower() for part in pure_path.parts[:-1]]
    if not parent_parts:
        return False
    if any(part in _E2E_DIR_EXACT for part in parent_parts):
        return True
    if "playwright" not in parent_parts:
        return False
    return _has_tests_ancestor(pure_path) or pure_path.parts[:1] == ("tests",)


def _is_test_file_in_e2e_directory(*, pure_path: PurePosixPath) -> bool | None:
    """Return whether an E2E-directory path is test code, or None if outside one."""
    if not _path_has_e2e_directory(pure_path=pure_path):
        return None
    if _is_non_test_artifact(pure_path=pure_path):
        return False
    return _looks_like_test_code(pure_path=pure_path)


def _has_e2e_name_marker(*, name_lower: str) -> bool:
    """Return True when a basename uses a common E2E filename marker."""
    if ".e2e." in name_lower or ".e2e-" in name_lower or ".e2e_" in name_lower:
        return True
    return name_lower.endswith((".e2e.ts", ".e2e.tsx", ".e2e.js", ".e2e.jsx"))


def _test_name_matches_stem(*, name: str, source_stem: str) -> bool:
    """Return True when a test filename pairs with a source stem."""
    lower = name.lower()
    stem = source_stem.lower()
    prefixes = (
        f"test_{stem}.",
        f"{stem}_test.",
        f"{stem}.test.",
        f"{stem}.spec.",
    )
    return lower.startswith(prefixes) or lower == f"{stem}.bats"


def _strip_test_layer(parent: str) -> str:
    """Remove optional unit/integration layer from a tests/... parent path."""
    if not parent.startswith(("tests/", "__tests__/")):
        return parent
    parts = [part for part in parent.split("/")[1:] if part]
    if parts and parts[0] in _TEST_LAYER_PARTS:
        parts = parts[1:]
    return "/".join(parts)


def _package_local_test_mirror_match(
    *,
    test_parent: str,
    source_parent: str,
) -> bool | None:
    """Return whether a package-local ``tests/`` path mirrors ``source_parent``."""
    for tests_dir in ("tests", "__tests__"):
        marker = f"/{tests_dir}/"
        idx = test_parent.rfind(marker)
        if idx == -1:
            continue
        pkg_prefix = test_parent[:idx]
        if not pkg_prefix:
            continue
        mirrored = test_parent[idx + len(marker) :]
        if not mirrored:
            return source_parent == f"{pkg_prefix}/src"
        mirrored_parts = mirrored.split("/")
        if mirrored_parts[0] in _TEST_LAYER_PARTS:
            if len(mirrored_parts) == 1:
                return source_parent == f"{pkg_prefix}/src"
            suffix_path = "/".join(mirrored_parts[1:])
            return source_parent == f"{pkg_prefix}/src/{suffix_path}"
        return source_parent == f"{pkg_prefix}/src/{mirrored}"
    return None


def _parents_compatible(*, test_path: str, source_path: str) -> bool:
    """Return True when test and source paths are directory-related."""
    test_pure = PurePosixPath(test_path.replace("\\", "/"))
    source_pure = PurePosixPath(source_path.replace("\\", "/"))
    test_parent = test_pure.parent.as_posix()
    source_parent = source_pure.parent.as_posix()

    if test_parent == source_parent:
        return True

    if source_parent == ".":
        return test_parent in {".", "tests", "__tests__"}

    if test_parent in {"tests", "__tests__"}:
        return source_parent == "src"

    if test_parent.startswith(("tests/", "__tests__/")):
        mirrored = _strip_test_layer(test_parent)
        if not mirrored:
            return source_parent == "src"
        if source_parent == f"src/{mirrored}":
            return True
        source_parts = tuple(part for part in source_parent.split("/") if part)
        test_parts = tuple(part for part in mirrored.split("/") if part)
        if not test_parts:
            return False
        if len(test_parts) < 2:
            return False
        if (
            source_parts
            and source_parts[0].startswith("src")
            and source_parts[0] != "src"
        ):
            return False
        return source_parts[-len(test_parts) :] == test_parts

    package_match = _package_local_test_mirror_match(
        test_parent=test_parent,
        source_parent=source_parent,
    )
    if package_match is not None:
        return package_match

    if test_parent.endswith(("/tests", "/__tests__")):
        if test_pure.parent.parent.as_posix() == source_parent:
            return True
        if source_parent.endswith("/src"):
            return test_pure.parent.parent == source_pure.parent.parent

    return False


def matches_test_for_source(
    *,
    test_path: str,
    source_stem: str,
    source_path: str | None = None,
) -> bool:
    """Return True when ``test_path`` appears to test ``source_stem``.

    Args:
        test_path: Candidate test file path.
        source_stem: Source file stem without extension.
        source_path: Optional full source path for disambiguation.

    Returns:
        True when the test path explicitly pairs with the source stem.
    """
    if not is_test_path(test_path):
        return False

    test_pure = PurePosixPath(test_path.replace("\\", "/"))
    if not _test_name_matches_stem(name=test_pure.name, source_stem=source_stem):
        return False

    if source_path is None:
        return True

    return _parents_compatible(test_path=test_path, source_path=source_path)
