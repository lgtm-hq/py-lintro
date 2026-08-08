"""Pin the #724 / AC10 core → AI import boundary.

Epic #1972 acceptance criterion 10: no new import edge from core configuration
or execution packages into AI internals. ``tests/unit/test_package_imports.py``
only checks that packages import; it does not enforce the direction of the
edge. ``tests/unit/utils/test_output_ai_import_isolation.py`` already pins the
output / tool-executor seams at import time.

This module statically rejects *runtime* ``lintro.ai`` imports under the core
packages listed below. ``TYPE_CHECKING``-only imports remain allowed because
they do not load the AI layer at runtime.
"""

from __future__ import annotations

import ast
from pathlib import Path

from assertpy import assert_that

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LINTRO_ROOT = PROJECT_ROOT / "lintro"

# Packages / modules that form the core configuration and execution surface.
# Adapters (CLI commands, MCP toolkits, doctor report) may import AI; these
# packages must not.
_CORE_PREFIXES: tuple[str, ...] = (
    "config/",
    "models/",
    "enums/",
    "plugins/",
    "parsers/",
    "formatters/",
    "utils/execution/",
    "utils/output/",
    "utils/console/",
    "utils/unified_config.py",
    "utils/tool_executor.py",
    "utils/tool_metadata.py",
)


def _is_core_path(relative: str) -> bool:
    """Return whether ``relative`` is under a guarded core package.

    Args:
        relative: Path relative to ``lintro/`` using forward slashes.

    Returns:
        True when the path is in the AC10 core surface.
    """
    for prefix in _CORE_PREFIXES:
        if prefix.endswith(".py"):
            if relative == prefix:
                return True
        elif relative == prefix.rstrip("/") or relative.startswith(prefix):
            return True
    return False


def _runtime_ai_imports(path: Path) -> list[tuple[int, str]]:
    """Collect runtime (non-TYPE_CHECKING) imports of ``lintro.ai`` from ``path``.

    Args:
        path: Python source file to scan.

    Returns:
        List of ``(lineno, module)`` pairs for forbidden runtime imports.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[tuple[int, str]] = []

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self._in_type_checking = False

        def visit_If(self, node: ast.If) -> None:
            test = node.test
            is_type_checking = (
                isinstance(test, ast.Name) and test.id == "TYPE_CHECKING"
            ) or (
                isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
            )
            if is_type_checking:
                previous = self._in_type_checking
                self._in_type_checking = True
                for child in node.body:
                    self.visit(child)
                self._in_type_checking = previous
                for child in node.orelse:
                    self.visit(child)
                return
            self.generic_visit(node)

        def visit_Import(self, node: ast.Import) -> None:
            if self._in_type_checking:
                return
            for alias in node.names:
                if alias.name == "lintro.ai" or alias.name.startswith("lintro.ai."):
                    hits.append((node.lineno, alias.name))

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            if self._in_type_checking:
                return
            module = node.module or ""
            if module == "lintro.ai" or module.startswith("lintro.ai."):
                hits.append((node.lineno, module))

    _Visitor().visit(tree)
    return hits


def test_core_packages_have_no_runtime_ai_imports() -> None:
    """Core configuration/execution packages must not import ``lintro.ai``."""
    violations: list[str] = []
    for path in sorted(LINTRO_ROOT.rglob("*.py")):
        relative = path.relative_to(LINTRO_ROOT).as_posix()
        if not _is_core_path(relative):
            continue
        for lineno, module in _runtime_ai_imports(path):
            violations.append(f"{relative}:{lineno} imports {module}")

    assert_that(violations).is_empty()


def test_config_package_loads_without_ai_modules() -> None:
    """Importing ``lintro.config`` must not load any ``lintro.ai`` module."""
    import subprocess  # nosec B404 - fixed argv against this interpreter
    import sys

    snippet = (
        "import sys, lintro.config; "
        "print(','.join(sorted(m for m in sys.modules "
        "if m.startswith('lintro.ai'))))"
    )
    completed = subprocess.run(  # nosec B603 - fixed argv, shell=False
        [sys.executable, "-c", snippet],
        capture_output=True,
        text=True,
        check=True,
    )
    loaded = [name for name in completed.stdout.strip().split(",") if name]
    assert_that(loaded).is_empty()
