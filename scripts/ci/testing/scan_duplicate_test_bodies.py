#!/usr/bin/env python3
"""Report test functions in ``tests/`` that share a normalised body.

Earlier file splits copied test functions into their new home without deleting
the monolith copy, leaving groups of byte-identical tests that cost runtime and
drift apart silently. This scanner hashes every test function body with names
and constants preserved but leading docstrings and position information
stripped, so a copy that was only re-worded still matches, then reports every
group of two or more functions whose bodies hash the same.

Run as a script for a human-readable report, or import :func:`find_duplicate_groups`
from a test to assert the count stays at zero.
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class TestFunction:
    """One test function found by the scanner.

    Attributes:
        path: Repository-relative path of the file defining the function.
        name: Function name.
        lineno: 1-based line number of the ``def`` statement.
        body_hash: Normalised hash of the function body.
        context: Normalised definitions of the module-level names the body
            reads, so two identical bodies over different constants or imports
            are not reported as duplicates.
    """

    path: str
    name: str
    lineno: int
    body_hash: str
    context: tuple[tuple[str, str], ...]


def _normalise(node: ast.AST) -> str:
    """Dump an AST node without position information.

    Args:
        node: Node to dump.

    Returns:
        A stable textual representation of the node.
    """
    return ast.dump(node, annotate_fields=True, include_attributes=False)


def _body_signature(func: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Build the normalised signature used to compare two test bodies.

    The decorator list and argument names are included so that a test which
    merely shares a body with a differently parametrised sibling is not counted
    as a duplicate.

    Args:
        func: Function definition node.

    Returns:
        The normalised signature string.
    """
    parts = [_normalise(decorator) for decorator in func.decorator_list]
    parts.append(_normalise(func.args))
    body = func.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    parts.extend(_normalise(statement) for statement in body)
    return "\n".join(parts)


def _display_path(*, path: Path) -> str:
    """Render a path relative to the repository when possible.

    Args:
        path: File path to render.

    Returns:
        The repository-relative path, or the absolute path when the file lies
        outside the repository (``--root`` may point anywhere).
    """
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _module_bindings(tree: ast.Module) -> dict[str, str]:
    """Map every module-level name to a normalised definition.

    Two functions with the same body still behave differently when a module
    constant or import they reference differs — ``_SCRIPT`` pointing at a
    different shell script, for instance. Recording the definitions lets the
    scanner compare them before calling a cross-file pair a duplicate.

    Args:
        tree: Parsed module.

    Returns:
        A mapping of bound name to the normalised source of its binding.
    """
    bindings: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bindings[target.id] = _normalise(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            bindings[node.target.id] = _normalise(node.value) if node.value else ""
        elif isinstance(node, ast.Import | ast.ImportFrom):
            module = getattr(node, "module", None) or ""
            level = getattr(node, "level", 0)
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                bindings[bound] = f"import {'.' * level}{module}:{alias.name}"
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            bindings[node.name] = _normalise(node)
    return bindings


def _referenced_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Collect every plain name a function reads.

    Args:
        func: Function definition node.

    Returns:
        The names loaded anywhere in the function, decorators included.
    """
    return {
        node.id
        for node in ast.walk(func)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }


def _iter_test_functions(path: Path) -> list[TestFunction]:
    """Collect the test functions defined in one file.

    Args:
        path: File to parse.

    Returns:
        The test functions defined at any nesting level in the file.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    bindings = _module_bindings(tree=tree)
    found: list[TestFunction] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if not node.name.startswith("test_"):
            continue
        signature = _body_signature(func=node)
        if len(signature.splitlines()) < 2:
            continue
        context = {
            name: bindings[name]
            for name in sorted(_referenced_names(func=node))
            if name in bindings
        }
        found.append(
            TestFunction(
                path=_display_path(path=path),
                name=node.name,
                lineno=node.lineno,
                body_hash=signature,
                context=tuple(sorted(context.items())),
            ),
        )
    return found


def find_duplicate_groups(
    *,
    root: Path | None = None,
) -> list[list[TestFunction]]:
    """Find groups of test functions sharing a normalised body.

    Args:
        root: Directory to scan. Defaults to the repository ``tests`` tree.

    Returns:
        One list per duplicate group, each holding two or more functions,
        sorted by path and line number.

    Raises:
        NotADirectoryError: If ``root`` is not an existing directory. Left to
            ``rglob`` this would yield nothing and report a clean gate, so a
            typo in CI would silently pass (#2375).
    """
    scan_root = root if root is not None else REPO_ROOT / "tests"
    if not scan_root.is_dir():
        raise NotADirectoryError(f"not a directory: {scan_root}")
    buckets: dict[tuple[str, tuple[tuple[str, str], ...]], list[TestFunction]] = (
        defaultdict(list)
    )
    for path in sorted(scan_root.rglob("*.py")):
        for function in _iter_test_functions(path=path):
            buckets[(function.body_hash, function.context)].append(function)
    groups = [
        sorted(g, key=lambda f: (f.path, f.lineno))
        for g in buckets.values()
        if len(g) > 1
    ]
    return sorted(groups, key=lambda g: (g[0].path, g[0].lineno))


def main(argv: list[str] | None = None) -> int:
    """Print the duplicate groups and exit non-zero when any exist.

    Args:
        argv: Command-line arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        ``0`` when no duplicate group was found, ``1`` when at least one
        exists, and ``2`` when ``--root`` does not name a directory.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Directory to scan (default: the repository tests/ tree).",
    )
    args = parser.parse_args(argv)
    try:
        groups = find_duplicate_groups(root=args.root)
    except NotADirectoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for group in groups:
        print("duplicate group:")
        for function in group:
            print(f"  {function.path}:{function.lineno} {function.name}")
    print(f"{len(groups)} duplicate group(s)")
    return 1 if groups else 0


if __name__ == "__main__":
    sys.exit(main())
