"""One-hop Python import edges among changed files (#2154).

Only Python files participate. Edges are parsed from post-image content
and restricted to the changed-file set. A change to B invalidates A
when A imports B. A→B→C with only C changed re-enters B, not A.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import PurePosixPath

__all__ = ["importers_of", "parse_python_imports"]

_PYTHON_SUFFIXES = frozenset({".py", ".pyi"})


def parse_python_imports(*, source: str) -> frozenset[str]:
    """Return dotted module names imported by *source*.

    Args:
        source: Post-image Python text. Parse errors yield no edges
            (fail toward no extra invalidation, not fake coverage).

    Returns:
        Dotted module names from ``import`` and ``from`` statements.
        Relative imports keep their leading dots stripped and join
        remaining parts; they are resolved against the importer path
        by :func:`importers_of`.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return frozenset()
    collected: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name:
                    collected.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                collected.add("." * node.level + module)
            elif module:
                collected.add(module)
    return frozenset(collected)


def importers_of(
    *,
    changed_paths: set[str],
    contents: Mapping[str, str],
    directly_changed: set[str],
) -> dict[str, set[str]]:
    """Map each directly-changed path to one-hop importers.

    Args:
        changed_paths: All changed repository-relative paths.
        contents: Post-image text keyed by path (missing keys skipped).
        directly_changed: Paths whose patch hash changed this round.

    Returns:
        ``{changed_path: {importer, ...}}`` for Python importers that
        live in *changed_paths* and import a directly-changed file.
    """
    python_paths = {path for path in changed_paths if _is_python(path)}
    module_to_path = {_module_name(path): path for path in python_paths}
    reverse: dict[str, set[str]] = {path: set() for path in directly_changed}
    for importer in python_paths:
        source = contents.get(importer)
        if source is None:
            continue
        imported = _resolve_imports(
            importer=importer,
            raw_names=parse_python_imports(source=source),
            module_to_path=module_to_path,
        )
        for target in imported & directly_changed:
            if target != importer:
                reverse.setdefault(target, set()).add(importer)
    return reverse


def _is_python(path: str) -> bool:
    """Return whether *path* is a Python source file."""
    return PurePosixPath(path).suffix.lower() in _PYTHON_SUFFIXES


def _module_name(path: str) -> str:
    """Convert a repository path to a dotted module name."""
    posix = PurePosixPath(path)
    parts = list(posix.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_imports(
    *,
    importer: str,
    raw_names: frozenset[str],
    module_to_path: Mapping[str, str],
) -> set[str]:
    """Resolve imported names onto changed-file paths."""
    resolved: set[str] = set()
    importer_mod = _module_name(importer)
    importer_pkg = (
        importer_mod.rsplit(".", 1)[0] if "." in importer_mod else importer_mod
    )
    for raw in raw_names:
        if raw.startswith("."):
            level = len(raw) - len(raw.lstrip("."))
            remainder = raw[level:]
            pkg_parts = importer_pkg.split(".") if importer_pkg else []
            if level > 1:
                pkg_parts = pkg_parts[: -(level - 1)]
            if remainder:
                pkg_parts = [*pkg_parts, *remainder.split(".")]
            candidate = ".".join(part for part in pkg_parts if part)
        else:
            candidate = raw
        path = module_to_path.get(candidate)
        if path is not None:
            resolved.add(path)
            continue
        # ``from package import module`` may name a sibling file.
        if "." in candidate:
            continue
        dotted = f"{importer_pkg}.{candidate}" if importer_pkg else candidate
        path = module_to_path.get(dotted)
        if path is not None:
            resolved.add(path)
    return resolved
