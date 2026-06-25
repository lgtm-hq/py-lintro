"""Shared path heuristics for AI diff review."""

from __future__ import annotations

from pathlib import PurePosixPath

_TEST_SUFFIXES = (".spec.", ".test.", "_test.")


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
        or pure_path.match("**/tests/**")
        or pure_path.match("**/__tests__/**")
    )


def matches_test_for_source(*, test_path: str, source_stem: str) -> bool:
    """Return True when ``test_path`` appears to test ``source_stem``.

    Args:
        test_path: Candidate test file path.
        source_stem: Source file stem without extension.

    Returns:
        True when the test path explicitly pairs with the source stem.
    """
    if not is_test_path(test_path):
        return False

    name = PurePosixPath(test_path.replace("\\", "/")).name
    expected_names = {
        f"test_{source_stem}.py",
        f"{source_stem}_test.py",
        f"test_{source_stem}.rs",
        f"{source_stem}_test.rs",
        f"test_{source_stem}.bats",
        f"test_{source_stem}.ts",
        f"test_{source_stem}.js",
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
    return name in expected_names
