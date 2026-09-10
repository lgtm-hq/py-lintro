#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Report the intra-package import matrix for the ``lintro`` package.

The matrix answers one question that ``import-linter`` contracts alone cannot:
*how far* is the package from its declared layering. ``import-linter`` reports
"kept/broken" against a baseline of ignored edges; this script counts the raw
edges between top-level ``lintro/*`` packages and lists every two-way cycle,
which is the number the ratchet in ``tests/unit/test_import_boundaries.py``
holds down.

Both module-level and function-body (``lazy``) imports are counted. A lazy
import is a deferred edge, not a removed one, so hiding an import inside a
function must not make the matrix look better.

Usage:
    uv run python scripts/ci/import_matrix.py [--package-root lintro]

Exit codes:
    0 — the matrix was produced (this script never gates on its own)
    2 — usage error (the package root does not exist)
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT_PACKAGE = "lintro"


@dataclass(frozen=True)
class ImportMatrix:
    """Directed import edges between top-level members of a package.

    Attributes:
        root_package: Name of the package the matrix was built for.
        members: Sorted names of the top-level subpackages that were
            discovered.
        edges: Edge counts keyed by ``(importer, imported)``. Self-edges are
            excluded, so every key names two distinct members.
    """

    root_package: str
    members: tuple[str, ...]
    edges: dict[tuple[str, str], int] = field(default_factory=dict)

    def two_cycles(self) -> tuple[tuple[str, str], ...]:
        """Return every pair of members that import each other.

        Returns:
            tuple[tuple[str, str], ...]: Sorted ``(a, b)`` pairs with ``a < b``
                where both ``a -> b`` and ``b -> a`` edges exist.
        """
        pairs = {
            (left, right)
            for left, right in self.edges
            if left < right and (right, left) in self.edges
        }
        return tuple(sorted(pairs))


def discover_members(*, package_root: Path) -> tuple[str, ...]:
    """List the top-level subpackages of a package directory.

    Only subpackages count. Single-file modules that sit directly in the root
    (``lintro/cli.py``, ``lintro/_tool_versions.py``) are entry points and
    generated data rather than layers, and counting them would add cycles the
    layering contract does not describe.

    Args:
        package_root: Directory of the package (the one holding
            ``__init__.py``).

    Returns:
        tuple[str, ...]: Sorted subpackage names.
    """
    return tuple(
        sorted(
            entry.name
            for entry in package_root.iterdir()
            if entry.is_dir() and (entry / "__init__.py").exists()
        ),
    )


def _member_of(*, module_parts: tuple[str, ...], members: frozenset[str]) -> str | None:
    """Map a dotted module path to the top-level member that owns it.

    Args:
        module_parts: Dotted module path split on ``.``, starting with the
            root package name.
        members: Known top-level member names.

    Returns:
        str | None: The owning member, or ``None`` when the path names the
            root package itself or something outside it.
    """
    if len(module_parts) < 2 or module_parts[1] not in members:
        return None
    return module_parts[1]


def _owner_of_file(
    *,
    path: Path,
    package_root: Path,
    members: frozenset[str],
) -> str | None:
    """Return the top-level member that owns a source file.

    Args:
        path: Python source file inside the package.
        package_root: Directory of the package.
        members: Known top-level member names.

    Returns:
        str | None: The owning member, or ``None`` for the package's own
            top-level dunder modules.
    """
    head = path.relative_to(package_root).parts[0]
    return head if head in members else None


def _targets_from_node(
    *,
    node: ast.Import | ast.ImportFrom,
    owner_module: tuple[str, ...],
    root_package: str,
    members: frozenset[str],
) -> list[str]:
    """Resolve one import statement to the members it depends on.

    Args:
        node: The import statement.
        owner_module: Dotted path of the module containing the statement.
        root_package: Name of the root package.
        members: Known top-level member names.

    Returns:
        list[str]: Members imported by this statement (possibly empty, and
            possibly repeated when one statement names several).
    """
    targets: list[str] = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            parts = tuple(alias.name.split("."))
            if parts and parts[0] == root_package:
                member = _member_of(module_parts=parts, members=members)
                if member is not None:
                    targets.append(member)
        return targets

    if node.level:
        base = owner_module[: len(owner_module) - node.level]
        parts = base + tuple((node.module or "").split(".") if node.module else ())
    else:
        parts = tuple((node.module or "").split("."))
    if not parts or parts[0] != root_package:
        return targets

    member = _member_of(module_parts=parts, members=members)
    if member is not None:
        targets.append(member)
        return targets
    if len(parts) == 1:
        # ``from lintro import enums, models``
        targets.extend(alias.name for alias in node.names if alias.name in members)
    return targets


def build_matrix(
    *,
    package_root: Path,
    root_package: str = ROOT_PACKAGE,
) -> ImportMatrix:
    """Build the import matrix for a package by walking its syntax trees.

    Every ``import`` and ``from`` statement is counted regardless of nesting,
    so function-body imports weigh exactly as much as module-level ones.

    Args:
        package_root: Directory of the package to analyse.
        root_package: Name of the package, used to recognise absolute imports.

    Returns:
        ImportMatrix: The edge counts and discovered members.
    """
    members = discover_members(package_root=package_root)
    member_set = frozenset(members)
    edges: dict[tuple[str, str], int] = {}

    for path in sorted(package_root.rglob("*.py")):
        owner = _owner_of_file(path=path, package_root=package_root, members=member_set)
        if owner is None:
            continue
        relative = path.relative_to(package_root).with_suffix("")
        owner_module = (root_package, *relative.parts)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Import | ast.ImportFrom):
                continue
            for target in _targets_from_node(
                node=node,
                owner_module=owner_module,
                root_package=root_package,
                members=member_set,
            ):
                if target == owner:
                    continue
                edges[(owner, target)] = edges.get((owner, target), 0) + 1

    return ImportMatrix(root_package=root_package, members=members, edges=edges)


def render(*, matrix: ImportMatrix) -> str:
    """Render the matrix and its cycles as plain text.

    Args:
        matrix: The matrix to render.

    Returns:
        str: A human-readable report ending with a newline.
    """
    lines = [f"Import matrix for `{matrix.root_package}` (row imports column)", ""]
    for importer in matrix.members:
        outgoing = sorted(
            (
                (imported, count)
                for (owner, imported), count in matrix.edges.items()
                if owner == importer
            ),
            key=lambda item: item[0],
        )
        if not outgoing:
            continue
        rendered = ", ".join(f"{imported}({count})" for imported, count in outgoing)
        lines.append(f"{importer} -> {rendered}")

    cycles = matrix.two_cycles()
    lines.extend(("", f"Two-way cycles: {len(cycles)}"))
    lines.extend(
        f"  {left} <-> {right} "
        f"({matrix.edges[(left, right)]}/{matrix.edges[(right, left)]})"
        for left, right in cycles
    )
    lines.append("")
    return "\n".join(lines)


def main(*, argv: list[str] | None = None) -> int:
    """Print the import matrix for the requested package.

    Args:
        argv: Command-line arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        int: Process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--package-root",
        type=Path,
        default=Path(__file__).resolve().parents[2] / ROOT_PACKAGE,
        help="Directory of the package to analyse (default: the repo's lintro/).",
    )
    args = parser.parse_args(argv)
    package_root: Path = args.package_root
    if not package_root.is_dir():
        print(f"error: not a directory: {package_root}", file=sys.stderr)
        return 2
    matrix = build_matrix(package_root=package_root, root_package=package_root.name)
    print(render(matrix=matrix), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
