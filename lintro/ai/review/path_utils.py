"""Shared path heuristics for AI diff review."""

from __future__ import annotations

from pathlib import PurePosixPath

_TEST_NAME_MARKERS = (".spec.", ".test.", "_test.")
_TEST_LAYER_PARTS: frozenset[str] = frozenset({"unit", "integration"})


def _has_tests_ancestor(path: PurePosixPath) -> bool:
    return any(part in ("tests", "__tests__") for part in path.parts[:-1])


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
    if _has_tests_ancestor(pure_path) or pure_path.parts[:1] == ("tests",):
        return True
    return name.startswith("test_") or any(
        marker in name for marker in _TEST_NAME_MARKERS
    )


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
