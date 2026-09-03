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
        "diff",
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


#: First path segments that mark a repository-level test tree.
_TOP_LEVEL_TESTS_ROOTS: frozenset[tuple[str, ...]] = frozenset(
    {("tests",), ("__tests__",)},
)


def _is_under_tests_directory(*, pure_path: PurePosixPath) -> bool:
    """Return True when a path sits under tests/ or __tests__."""
    return _has_tests_ancestor(pure_path) or pure_path.parts[:1] == ("tests",)


def _is_top_level_tests_root(*, pure_path: PurePosixPath) -> bool:
    """Return True when a path's first segment is ``tests`` or ``__tests__``."""
    return pure_path.parts[:1] in _TOP_LEVEL_TESTS_ROOTS


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


def is_source_code_path(path: str) -> bool:
    """Return True when a path names source code rather than docs, config, or data.

    Used when deciding whether a file can own a test: a changed
    ``docs/migrate-docs-content.md`` next to ``scripts/ci/site/migrate-docs-content.py``
    must not make the script's stem look ambiguous.

    Args:
        path: Repository-relative path.

    Returns:
        True when identify reports a meaningful language tag for the file
        name and the path is not a docs, config, or fixture artifact.
    """
    pure_path = PurePosixPath(path.replace("\\", "/"))
    if _is_non_test_artifact(pure_path=pure_path):
        return False
    return bool(_meaningful_source_identify_tags(name=pure_path.name))


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


def normalize_stem(*, stem: str) -> str:
    """Return a separator-insensitive form of a file stem.

    Hyphenated and underscored names describe the same module for pairing
    purposes (``migrate-docs-content.py`` is tested by
    ``test_migrate_docs_content.py``), so both are lower-cased and their
    separators are folded to ``_``.

    Args:
        stem: File stem or basename to normalise.

    Returns:
        The lower-cased stem with ``-`` folded to ``_``.
    """
    return stem.lower().replace("-", "_")


def _test_name_matches_stem(*, name: str, source_stem: str) -> bool:
    """Return True when a test filename pairs with a source stem.

    Both sides are normalised with :func:`normalize_stem` first, so hyphenated
    sources pair with underscored test filenames and vice versa. Exact matches
    are unaffected by the normalisation.

    Args:
        name: Test file basename.
        source_stem: Source file stem without its extension.

    Returns:
        True when the basename uses a conventional test naming pattern for the
        source stem.
    """
    lower = normalize_stem(stem=name)
    stem = normalize_stem(stem=source_stem)
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


def _tests_root_chain_compatible(
    *,
    mirrored: str,
    source_parent: str,
    allow_prefix: bool = False,
) -> bool:
    """Return True when a top-level ``tests/`` chain projects onto a source chain.

    ``mirrored`` is the test file's directory chain below the ``tests/`` (or
    ``__tests__/``) root with any ``unit``/``integration`` layer already
    stripped. Two deterministic projections are accepted:

    * **Prefix** — the test chain matches the leading segments of the source
      chain, so ``tests/scripts/ci/test_x.py`` and ``tests/scripts/test_x.py``
      both pair with ``scripts/ci/site/x.py``. Because the match is anchored on
      the source's own top-level directory, unrelated trees never pair (a
      ``lintro/`` source is not paired with a ``tests/scripts/`` test). This
      projection is looser than a mirror, so it is only offered when the
      caller passes ``allow_prefix=True``, which the chunker does exactly when
      no other source in the diff shares the stem; otherwise two same-stem
      sources under one tree would be paired by sort order.
    * **Suffix** — the test chain matches the trailing segments of the source
      chain, the existing mirror layout where ``tests/unit/ai/review`` pairs
      with ``lintro/ai/review``. Suffix matches require at least two segments so
      a single shared directory name cannot pair unrelated trees.

    Sources under a ``src``-like but non-canonical root (``src2/``) are rejected
    outright, preserving the existing guard against near-miss mirror roots.

    Args:
        mirrored: Test directory chain below the tests root.
        source_parent: POSIX directory chain of the source file.
        allow_prefix: Whether the prefix projection may be used.

    Returns:
        True when the two chains are compatible under an allowed projection.
    """
    source_parts = tuple(part for part in source_parent.split("/") if part)
    test_parts = tuple(part for part in mirrored.split("/") if part)
    if not test_parts or not source_parts:
        return False
    if source_parts[0].startswith("src") and source_parts[0] != "src":
        return False
    if allow_prefix and source_parts[: len(test_parts)] == test_parts:
        return True
    if len(test_parts) < 2:
        return False
    return source_parts[-len(test_parts) :] == test_parts


def _parents_compatible(
    *,
    test_path: str,
    source_path: str,
    allow_prefix: bool = False,
) -> bool:
    """Return True when test and source paths are directory-related.

    Args:
        test_path: Test file path.
        source_path: Source file path.
        allow_prefix: Whether the looser prefix projection under ``tests/``
            may pair the two; see :func:`_tests_root_chain_compatible`.

    Returns:
        True when the parents are compatible.
    """
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
        return _tests_root_chain_compatible(
            mirrored=mirrored,
            source_parent=source_parent,
            allow_prefix=allow_prefix,
        )

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


def _has_near_miss_source_root(*, path: str | None) -> bool:
    """Return True when a source path sits under a ``src``-like but non-``src`` root.

    Args:
        path: Source file path, or ``None`` when unknown.

    Returns:
        True for roots such as ``src2/`` or ``srcs/`` that must never pair.
    """
    if not path:
        return False
    parts = [part for part in path.replace("\\", "/").split("/") if part]
    return len(parts) > 1 and parts[0].startswith("src") and parts[0] != "src"


def matches_test_for_source(
    *,
    test_path: str,
    source_stem: str,
    source_path: str | None = None,
    stem_is_unique: bool = False,
) -> bool:
    """Return True when ``test_path`` appears to test ``source_stem``.

    Args:
        test_path: Candidate test file path.
        source_stem: Source file stem without extension.
        source_path: Optional full source path for disambiguation.
        stem_is_unique: When True, the caller has established that no other
            source under review shares this stem. A test under a tests root
            then pairs on the stem alone, which is the only case where
            unrelated top-level trees are allowed to pair.

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

    if _parents_compatible(
        test_path=test_path,
        source_path=source_path,
        allow_prefix=stem_is_unique,
    ):
        return True

    # The unique-stem fallback may cross directory trees, but never from a
    # near-miss source root such as ``src2/``: that guard exists to stop a
    # look-alike tree from claiming ``tests/`` files, and a unique stem does
    # not change what the root is.
    if _has_near_miss_source_root(path=source_path):
        return False
    # Only a top-level test root may pair across trees. A nested
    # ``other/tests/`` belongs to the ``other/`` tree, and
    # ``_parents_compatible`` has already rejected that tree above.
    return stem_is_unique and _is_top_level_tests_root(pure_path=test_pure)
