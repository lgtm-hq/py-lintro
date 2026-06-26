"""Shared path heuristics for AI diff review."""

from __future__ import annotations

from pathlib import PurePosixPath

_TEST_SUFFIXES = (".spec.", ".test.", "_test.")


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
    if name.startswith("test_") or name.endswith(".bats"):
        return True
    return (
        any(suffix in name for suffix in _TEST_SUFFIXES)
        or pure_path.parts[:1] == ("tests",)
        or _has_tests_ancestor(pure_path)
    )


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
    name = test_pure.name
    expected_names = {
        f"test_{source_stem}.py",
        f"{source_stem}_test.py",
        f"test_{source_stem}.rs",
        f"{source_stem}_test.rs",
        f"test_{source_stem}.bats",
        f"{source_stem}_test.bats",
        f"test_{source_stem}.ts",
        f"test_{source_stem}.js",
        f"{source_stem}_test.ts",
        f"{source_stem}_test.tsx",
        f"{source_stem}_test.js",
        f"{source_stem}_test.jsx",
        f"{source_stem}.spec.ts",
        f"{source_stem}.test.ts",
        f"{source_stem}.spec.tsx",
        f"{source_stem}.test.tsx",
        f"{source_stem}.spec.js",
        f"{source_stem}.test.js",
        f"{source_stem}.spec.jsx",
        f"{source_stem}.test.jsx",
        f"test_{source_stem}.tsx",
        f"test_{source_stem}.jsx",
    }
    if name not in expected_names:
        return False

    if (
        name.startswith(f"test_{source_stem}.")
        or name.startswith(f"{source_stem}_test.")
    ):
        return True

    if source_path is None:
        return True

    source_pure = PurePosixPath(source_path.replace("\\", "/"))
    source_parent = source_pure.parent.as_posix()
    if source_parent == ".":
        return True

    test_parent = test_pure.parent.as_posix()
    return (
        test_parent == source_parent
        or test_parent.endswith(f"/{source_parent}")
        or source_parent.endswith(f"/{test_parent}")
    )
