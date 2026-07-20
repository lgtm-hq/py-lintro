"""TypeScript tsconfig.json parsing, resolution, and project partitioning.

Provides utilities for:
- Parsing tsconfig.json files (with JSONC support via :mod:`lintro.utils.jsonc`)
- Resolving ``extends`` chains to compute effective include/exclude/files
- Discovering all tsconfig.json files in a project tree (via ``references``
  and directory walking)
- Partitioning discovered files across sub-projects ("deepest tsconfig wins")
- Creating temporary tsconfig.json files (shared by tsc and vue-tsc plugins)

These utilities are consumed by :mod:`lintro.tools.definitions.tsc` and
:mod:`lintro.tools.definitions.vue_tsc`.
"""

from __future__ import annotations

import fnmatch
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from loguru import logger

from lintro.utils.jsonc import (
    extract_tsconfig_fields,
    extract_type_roots,
    load_jsonc,
)
from lintro.utils.tsconfig_info import TsconfigInfo

__all__ = [
    "TsconfigInfo",
    "create_temp_tsconfig",
    "discover_tsconfigs",
    "enables_check_js",
    "has_explicit_scoping",
    "parse_tsconfig",
    "partition_files",
    "resolve_extends_chain",
]

# Config file names that are NOT used for type-checking by default.
# These are only included when explicitly referenced via ``references``.
_NON_CHECKING_CONFIGS: frozenset[str] = frozenset(
    {
        "tsconfig.build.json",
        "tsconfig.node.json",
        "tsconfig.emit.json",
    },
)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_tsconfig(path: Path) -> TsconfigInfo:
    """Parse a single tsconfig.json file.

    Args:
        path: Path to the tsconfig.json file.

    Returns:
        Populated :class:`TsconfigInfo` (without ``extends`` resolution).
    """
    abs_path = path.resolve()
    project_dir = abs_path.parent

    try:
        content = load_jsonc(abs_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.debug("[tsconfig] Failed to parse {}: {}", abs_path, exc)
        return TsconfigInfo(path=abs_path, project_dir=project_dir)

    if not isinstance(content, dict):
        return TsconfigInfo(path=abs_path, project_dir=project_dir)

    fields = extract_tsconfig_fields(content, project_dir)

    return TsconfigInfo(
        path=abs_path,
        project_dir=project_dir,
        include_patterns=fields["include"],
        exclude_patterns=fields["exclude"],
        files_list=fields["files"],
        references=[Path(r) for r in fields["references"]],
        is_composite=fields["composite"],
        raw_config=content,
    )


# ---------------------------------------------------------------------------
# Extends resolution
# ---------------------------------------------------------------------------


def resolve_extends_chain(
    path: Path,
    *,
    _seen: set[str] | None = None,
) -> TsconfigInfo:
    """Recursively resolve the ``extends`` chain for a tsconfig.

    Walks up the ``extends`` hierarchy to compute the effective
    ``include``, ``exclude``, and ``files`` fields.  The child's values
    override the parent's (matching TypeScript semantics).

    Circular references are detected and short-circuited.

    Args:
        path: Path to the tsconfig.json file.
        _seen: Internal set for cycle detection.

    Returns:
        A :class:`TsconfigInfo` with effective fields merged from the chain.
    """
    if _seen is None:
        _seen = set()

    abs_path = str(path.resolve())
    if abs_path in _seen:
        logger.warning("[tsconfig] Circular extends detected: {}", abs_path)
        return TsconfigInfo(path=Path(abs_path), project_dir=Path(abs_path).parent)

    _seen.add(abs_path)

    info = parse_tsconfig(path)
    extends_val = info.raw_config.get("extends") if info.raw_config else None

    if extends_val is None:
        return info

    # Collect parent configs (TS 5.0+ supports array extends)
    extends_list: list[str] = []
    if isinstance(extends_val, str):
        extends_list = [extends_val]
    elif isinstance(extends_val, list):
        extends_list = [v for v in extends_val if isinstance(v, str)]

    # Resolve parents and merge (later parents override earlier ones,
    # child overrides everything).  None means "not set"; [] means
    # "explicitly empty" — a child can clear a parent by setting [].
    merged_include: list[str] | None = None
    merged_exclude: list[str] | None = None
    merged_files: list[str] | None = None

    for ext in extends_list:
        parent_path = _resolve_extends_path(ext, info.project_dir)
        if parent_path is None:
            continue
        # Pass a copy so sibling extends branches don't share visited state
        parent_info = resolve_extends_chain(parent_path, _seen=set(_seen))
        # Parent values become the base (None means parent didn't set it)
        if parent_info.include_patterns is not None:
            merged_include = parent_info.include_patterns
        if parent_info.exclude_patterns is not None:
            merged_exclude = parent_info.exclude_patterns
        if parent_info.files_list is not None:
            merged_files = parent_info.files_list

    # Child overrides parent if it explicitly set the field ([] clears parent)
    if info.include_patterns is not None:
        merged_include = info.include_patterns
    if info.exclude_patterns is not None:
        merged_exclude = info.exclude_patterns
    if info.files_list is not None:
        merged_files = info.files_list

    return TsconfigInfo(
        path=info.path,
        project_dir=info.project_dir,
        include_patterns=merged_include,
        exclude_patterns=merged_exclude,
        files_list=merged_files,
        references=info.references,
        is_composite=info.is_composite,
        raw_config=info.raw_config,
    )


def _resolve_extends_path(extends: str, base_dir: Path) -> Path | None:
    """Resolve an ``extends`` value to an absolute path.

    Args:
        extends: The extends value (relative path or package name).
        base_dir: Directory of the config file containing the extends.

    Returns:
        Resolved path or ``None`` if unresolvable.
    """
    # Relative paths
    if extends.startswith((".", "/")):
        candidate = (base_dir / extends).resolve()
        # TypeScript appends .json if missing
        if candidate.exists():
            return candidate
        with_json = candidate.with_suffix(".json")
        if with_json.exists():
            return with_json
        return None

    # Node module resolution — walk ancestors to find hoisted packages
    # (e.g. "@tsconfig/node18/tsconfig.json" may live in a monorepo root).
    # When the resolved path is a package directory, follow TypeScript's
    # behaviour: read package.json's ``tsconfig`` field if present, else
    # fall back to ``<package>/tsconfig.json``.  Always return a file
    # path — never a directory — so callers can read it directly.
    ancestor = base_dir
    while True:
        node_modules = ancestor / "node_modules" / extends
        if node_modules.is_file():
            return node_modules.resolve()
        if node_modules.is_dir():
            resolved = _resolve_package_tsconfig(node_modules)
            if resolved is not None:
                return resolved
        with_json = node_modules.with_suffix(".json")
        if with_json.is_file():
            return with_json.resolve()
        parent = ancestor.parent
        if parent == ancestor:
            break
        ancestor = parent

    return None


def _resolve_package_tsconfig(package_dir: Path) -> Path | None:
    """Resolve a tsconfig file path inside a node_modules package directory.

    Honours TypeScript's lookup order: ``package.json``'s ``tsconfig`` field
    first, then ``tsconfig.json`` inside the package.

    Args:
        package_dir: Directory of an installed node package.

    Returns:
        Resolved path to a tsconfig file, or ``None`` if none found.
    """
    package_json = package_dir / "package.json"
    if package_json.is_file():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = None
        if isinstance(data, dict):
            tsconfig_field = data.get("tsconfig")
            if isinstance(tsconfig_field, str):
                candidate = (package_dir / tsconfig_field).resolve()
                if candidate.is_file():
                    return candidate
                with_json = candidate.with_suffix(".json")
                if with_json.is_file():
                    return with_json
    fallback = package_dir / "tsconfig.json"
    if fallback.is_file():
        return fallback.resolve()
    return None


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discover_tsconfigs(
    root: Path,
    exclude_patterns: list[str] | None = None,
    *,
    basenames: list[str] | None = None,
) -> list[TsconfigInfo]:
    """Discover all TypeScript sub-projects in a directory tree.

    Strategy:

    1. If the root tsconfig has ``references``, follow them recursively.
    2. Walk the directory tree for ``tsconfig.json`` files, respecting
       *exclude_patterns*.
    3. Deduplicate — configs found via references take precedence.
    4. Filter non-checking configs (``tsconfig.build.json``, etc.) unless
       they were explicitly referenced.
    5. Sort by path depth (deepest first) for "deepest wins" partitioning.

    Args:
        root: Root directory to search.
        exclude_patterns: Gitignore-style patterns to skip directories.
        basenames: Tsconfig filenames to look for during the directory walk.
            Defaults to ``["tsconfig.json", "tsconfig.app.json"]``.

    Returns:
        List of :class:`TsconfigInfo` sorted deepest-first.
    """
    root = root.resolve()
    exclude_patterns = exclude_patterns or []

    # Phase 1: Follow references from root tsconfig
    ref_configs: dict[str, TsconfigInfo] = {}
    ref_seen: set[str] = set()
    root_tsconfig = root / "tsconfig.json"
    if root_tsconfig.exists():
        _collect_references(root_tsconfig, ref_configs, _seen=ref_seen)

    # Phase 2: Walk the directory tree
    walked_configs: dict[str, TsconfigInfo] = {}
    _walk_for_tsconfigs(
        root,
        walked_configs,
        exclude_patterns,
        basenames=basenames,
    )

    # Phase 2b: Collect references from each walked tsconfig too.  Without
    # this, a non-checking config (e.g. ``tsconfig.build.json``) referenced
    # only by a sub-project would be filtered out in Phase 4 because it was
    # never recorded as ref-discovered.
    for walked_path in list(walked_configs):
        _collect_references(Path(walked_path), ref_configs, _seen=ref_seen)

    # Phase 3: Merge — references take precedence
    all_configs: dict[str, TsconfigInfo] = {}
    all_configs.update(walked_configs)
    all_configs.update(ref_configs)  # overwrite with ref versions

    # Phase 4: Filter non-checking configs (unless found via references)
    result: list[TsconfigInfo] = []
    for key, info in all_configs.items():
        filename = info.path.name
        if filename in _NON_CHECKING_CONFIGS and key not in ref_configs:
            continue
        result.append(info)

    # Phase 5: Sort deepest-first (more path parts = deeper)
    result.sort(key=lambda info: len(info.path.parts), reverse=True)

    return result


def _collect_references(
    tsconfig_path: Path,
    result: dict[str, TsconfigInfo],
    *,
    _seen: set[str],
) -> None:
    """Recursively collect tsconfigs from ``references`` arrays.

    Args:
        tsconfig_path: Path to a tsconfig.json file.
        result: Accumulator dict keyed by resolved path string.
        _seen: Cycle detection set.
    """
    abs_path = str(tsconfig_path.resolve())
    if abs_path in _seen:
        return
    _seen.add(abs_path)

    info = resolve_extends_chain(tsconfig_path)
    result[abs_path] = info

    for ref_path in info.references:
        _collect_references(ref_path, result, _seen=_seen)


def _walk_for_tsconfigs(
    root: Path,
    result: dict[str, TsconfigInfo],
    exclude_patterns: list[str],
    *,
    basenames: list[str] | None = None,
) -> None:
    """Walk the directory tree finding tsconfig files.

    Skips ``node_modules``, ``.git``, and directories matching
    *exclude_patterns*.

    Args:
        root: Root directory to walk.
        result: Accumulator dict keyed by resolved path string.
        exclude_patterns: Patterns to exclude.
        basenames: Filenames to look for. Defaults to
            ``["tsconfig.json", "tsconfig.app.json"]``.  Callers (e.g. the
            vue-tsc plugin) can pass a different list to discover
            framework-specific configs.
    """
    if basenames is None:
        basenames = ["tsconfig.json", "tsconfig.app.json"]
    always_skip = {
        "node_modules",
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        "dist",
        "build",
    }

    for dirpath, dirnames, filenames in os.walk(root):
        # Prune excluded directories in-place — match both basename and
        # relative path so gitignore-style patterns like "packages/*/dist" work
        dirnames[:] = [
            d
            for d in dirnames
            if d not in always_skip
            and not d.startswith(".")
            and not any(
                fnmatch.fnmatch(d, pat)
                or fnmatch.fnmatch(
                    Path(os.path.relpath(os.path.join(dirpath, d), root)).as_posix(),
                    pat,
                )
                for pat in exclude_patterns
            )
        ]

        for basename in basenames:
            if basename in filenames:
                tsconfig_path = Path(dirpath) / basename
                abs_key = str(tsconfig_path.resolve())
                if abs_key not in result:
                    result[abs_key] = resolve_extends_chain(tsconfig_path)


# ---------------------------------------------------------------------------
# Partitioning
# ---------------------------------------------------------------------------


def partition_files(
    files: list[str],
    tsconfigs: list[TsconfigInfo],
    *,
    log_label: str = "tsconfig",
) -> list[tuple[TsconfigInfo | None, list[str]]]:
    """Assign files to their governing tsconfig ("deepest wins").

    Each file is assigned to the deepest :class:`TsconfigInfo` whose
    ``project_dir`` contains the file.  If a parent tsconfig also governs
    a file already claimed by a child, the file is stripped from the parent.

    Files not governed by any tsconfig are grouped under ``None``.

    Expects *tsconfigs* to be sorted deepest-first (as returned by
    :func:`discover_tsconfigs`).

    Args:
        files: Absolute file paths to partition.
        tsconfigs: Discovered tsconfigs, deepest-first.
        log_label: Prefix used in the overlap-stripping log line so the
            calling tool (e.g. ``"tsc"`` or ``"vue-tsc"``) is identifiable.

    Returns:
        List of ``(tsconfig_info_or_none, files)`` tuples.
    """
    claimed: set[str] = set()
    partitions: dict[str | None, list[str]] = {}
    tsconfig_map: dict[str | None, TsconfigInfo | None] = {}

    for info in tsconfigs:
        project_key = str(info.path)
        tsconfig_map[project_key] = info
        partitions[project_key] = []

    # Assign files to deepest matching tsconfig
    for filepath in files:
        abs_file = os.path.abspath(filepath)
        assigned = False
        for info in tsconfigs:
            try:
                Path(abs_file).relative_to(info.project_dir)
            except ValueError:
                continue
            # This file is under this tsconfig's project_dir
            if abs_file not in claimed:
                project_key = str(info.path)
                partitions[project_key].append(abs_file)
                claimed.add(abs_file)
                assigned = True
            break  # deepest match found (tsconfigs sorted deepest-first)

        if not assigned and abs_file not in claimed:
            partitions.setdefault(None, []).append(abs_file)

    # Log overlap stripping
    for info in tsconfigs:
        project_key = str(info.path)
        assigned_files = partitions.get(project_key, [])
        if not assigned_files:
            continue
        # Check if a parent tsconfig also covers this directory
        for parent_info in tsconfigs:
            if parent_info is info:
                continue
            try:
                info.project_dir.relative_to(parent_info.project_dir)
            except ValueError:
                continue
            # parent_info is a parent of info
            if assigned_files:
                logger.info(
                    "[{}] {} — skipping {} file(s) under {} (governed by {})",
                    log_label,
                    parent_info.path,
                    len(assigned_files),
                    info.project_dir,
                    info.path,
                )
            break

    # Build result list
    result: list[tuple[TsconfigInfo | None, list[str]]] = []
    for info in tsconfigs:
        project_key = str(info.path)
        files_for_project = partitions.get(project_key, [])
        result.append((info, files_for_project))

    # Add fallback group
    fallback_files = partitions.get(None, [])
    if fallback_files:
        result.append((None, fallback_files))

    return result


# ---------------------------------------------------------------------------
# Compiler-option helpers
# ---------------------------------------------------------------------------


def enables_check_js(
    path: Path,
    *,
    _seen: set[str] | None = None,
) -> bool:
    """Return whether effective ``compilerOptions.checkJs`` is ``true``.

    Walks the ``extends`` chain using TypeScript's per-key override semantics:
    a child's explicit ``checkJs`` wins; otherwise the nearest parent value is
    used. Circular ``extends`` graphs are short-circuited.

    Args:
        path: Path to a tsconfig.json (or extended config) file.
        _seen: Internal set for cycle detection.

    Returns:
        ``True`` when the effective ``checkJs`` option is enabled.
    """
    return _resolve_check_js_option(path, _seen=_seen) is True


def _resolve_check_js_option(
    path: Path,
    *,
    _seen: set[str] | None = None,
) -> bool | None:
    """Resolve effective ``checkJs`` from a tsconfig and its extends chain.

    Args:
        path: Path to a tsconfig file.
        _seen: Internal set for cycle detection.

    Returns:
        ``True``/``False`` when ``checkJs`` is set anywhere in the effective
        chain, or ``None`` when the option is unset.
    """
    if _seen is None:
        _seen = set()

    abs_path = str(path.resolve())
    if abs_path in _seen:
        return None
    _seen.add(abs_path)

    info = parse_tsconfig(path)
    parent_value: bool | None = None
    extends_val = info.raw_config.get("extends") if info.raw_config else None

    extends_list: list[str] = []
    if isinstance(extends_val, str):
        extends_list = [extends_val]
    elif isinstance(extends_val, list):
        extends_list = [v for v in extends_val if isinstance(v, str)]

    for ext in extends_list:
        parent_path = _resolve_extends_path(ext, info.project_dir)
        if parent_path is None:
            continue
        # Later parents override earlier ones (TS 5.0+ array extends).
        resolved = _resolve_check_js_option(parent_path, _seen=set(_seen))
        if resolved is not None:
            parent_value = resolved

    comp_opts = info.raw_config.get("compilerOptions") if info.raw_config else None
    if isinstance(comp_opts, dict) and "checkJs" in comp_opts:
        return bool(comp_opts["checkJs"])
    return parent_value


# ---------------------------------------------------------------------------
# Scoping predicate
# ---------------------------------------------------------------------------


def has_explicit_scoping(info: TsconfigInfo) -> bool:
    """Return whether the tsconfig has explicit file scoping.

    A tsconfig has explicit scoping when ``include``, ``files``, or
    ``exclude`` was provided in the config (or anywhere in its ``extends``
    chain) — including when the value is an explicit empty list (e.g.
    ``"files": []``).  Lintro should respect this rather than overriding
    with all discovered files.  An explicit empty list still counts as
    explicit scoping because the user has signaled intent: a child config
    may use ``[]`` to clear a parent's value, and a temp tsconfig would
    silently undo that.  ``exclude`` counts for the same reason — a temp
    tsconfig would clear the exclusions, which is equally incorrect.

    The check uses ``is not None`` rather than truthiness so that an
    explicit empty list is distinguished from an absent field.

    Args:
        info: Parsed tsconfig metadata.

    Returns:
        ``True`` if ``include_patterns``, ``files_list``, or
        ``exclude_patterns`` is not ``None`` (i.e. was explicitly set,
        possibly to an empty list).
    """
    return (
        info.include_patterns is not None
        or info.files_list is not None
        or info.exclude_patterns is not None
    )


# ---------------------------------------------------------------------------
# Shared temp-tsconfig creation
# ---------------------------------------------------------------------------


def create_temp_tsconfig(
    base_tsconfig: Path,
    files: list[str],
    cwd: Path,
    *,
    prefix: str = ".lintro-tsc-",
    tool_label: str = "tsc",
) -> Path:
    """Create a temporary tsconfig.json extending a base config.

    Shared implementation used by both tsc and vue-tsc plugins.

    The temp config inherits ``compilerOptions`` from the base via
    ``extends`` and overrides ``include`` with the provided file list.
    Creates the temp file next to the base config when possible, falling
    back to the system temp directory for read-only filesystems.

    Args:
        base_tsconfig: Path to the original tsconfig.json to extend.
        files: File paths to include (relative to *cwd*).
        cwd: Working directory for resolving paths.
        prefix: Filename prefix for the temp file.
        tool_label: Label used in log messages (``"tsc"`` or ``"vue-tsc"``).

    Returns:
        Path to the temporary tsconfig.json file.

    Raises:
        OSError: If the temporary file cannot be created or written.
    """
    abs_base = base_tsconfig.resolve()

    # Convert relative file paths to absolute paths since the temp tsconfig
    # may be in a different directory than cwd
    abs_files = [str((cwd / f).resolve()) for f in files]

    compiler_options: dict[str, Any] = {
        # Ensure noEmit is set (type checking only)
        "noEmit": True,
    }

    # Read typeRoots from the base tsconfig once, up-front, and reuse the
    # extracted value in both the main and the read-only-fallback paths
    # below.  TypeScript resolves typeRoots relative to the config file,
    # so we resolve them to absolute paths here because the temp config
    # lives in a different directory.
    resolved_roots: list[str] | None = None
    try:
        base_content = load_jsonc(abs_base.read_text(encoding="utf-8"))
        resolved_roots = extract_type_roots(base_content, abs_base.parent)
    except (json.JSONDecodeError, OSError) as exc:
        logger.debug(
            "[{}] Could not read typeRoots from {}: {}",
            tool_label,
            abs_base,
            exc,
        )
    if resolved_roots is not None:
        compiler_options["typeRoots"] = resolved_roots

    temp_config = {
        "extends": str(abs_base),
        "include": abs_files,
        "exclude": [],
        "compilerOptions": compiler_options,
    }

    # Create temp file next to the base tsconfig so TypeScript can resolve
    # types/typeRoots by walking up from the temp file to node_modules.
    # Falls back to system temp dir with explicit typeRoots for read-only
    # filesystems (e.g. Docker volume mounts).
    try:
        fd, temp_path = tempfile.mkstemp(
            suffix=".json",
            prefix=prefix,
            dir=abs_base.parent,
        )
    except OSError:
        fd, temp_path = tempfile.mkstemp(
            suffix=".json",
            prefix=prefix.lstrip("."),
        )
        # Preserve existing typeRoots from the base tsconfig and add
        # the default node_modules/@types path so TypeScript can still
        # resolve type packages from the system temp dir.  Reuse the
        # already-extracted typeRoots from the up-front read above
        # instead of re-parsing the file.
        existing_type_roots: list[str] = (
            list(resolved_roots) if resolved_roots is not None else []
        )
        type_roots_explicit = resolved_roots is not None
        default_root = str(cwd / "node_modules" / "@types")
        # Add the default root when typeRoots was absent or had
        # entries (the temp file lives outside the project tree so
        # TypeScript cannot discover it by walking up).  When the
        # user explicitly set typeRoots: [] to disable global types,
        # honour that intent and leave the list empty.
        if (
            not type_roots_explicit or existing_type_roots
        ) and default_root not in existing_type_roots:
            existing_type_roots.append(default_root)
        compiler_options["typeRoots"] = existing_type_roots

    try:
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(temp_config, f, indent=2)
    except OSError:
        # Clean up on failure
        Path(temp_path).unlink(missing_ok=True)
        raise

    logger.debug(
        "[{}] Created temp tsconfig at {} extending {} with {} files",
        tool_label,
        temp_path,
        abs_base,
        len(files),
    )
    return Path(temp_path)
