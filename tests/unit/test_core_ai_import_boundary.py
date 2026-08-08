"""AC10 guard: core configuration/execution packages must not import ``lintro.ai``.

Epic #1972 acceptance criterion 10 and the #724 boundary: core packages resolve
and execute without pulling AI internals. ``tests/unit/test_package_imports.py``
only checks that packages are importable and listed in ``pyproject.toml``; it
does not enforce the import edge.

Narrower guards already exist for the execute/render path
(``tests/unit/utils/output/test_sarif_ai_seam.py``,
``tests/unit/utils/test_output_ai_import_isolation.py``). This module extends
the same AST check to the remaining core packages named by AC10.
"""

from __future__ import annotations

import ast
from pathlib import Path

from assertpy import assert_that

import lintro

_PACKAGE_ROOT = Path(lintro.__file__).parent

# Core packages that must stay free of runtime ``lintro.ai`` imports. Adapter
# surfaces (CLI review/doctor, MCP, API pipeline, idiom-review tool) and
# doctor_report's lazy AI probes are intentionally excluded — they *are* AI
# consumers.
_GUARDED_RELATIVE_PATHS: tuple[str, ...] = (
    "config",
    "models",
    "parsers",
    "enums",
    "formatters",
    "plugins",
    "utils/tool_executor.py",
    "utils/json_output.py",
    "utils/execution",
    "utils/output",
)


def _is_type_checking(test: ast.expr) -> bool:
    """Whether an ``if`` test is the ``TYPE_CHECKING`` guard.

    Args:
        test: The condition expression of an ``if`` statement.

    Returns:
        True when the branch only runs for static type checkers.
    """
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _is_ai_module(name: str) -> bool:
    """Whether a dotted module name is the AI package or lives inside it.

    Args:
        name: Dotted module name from an import statement.

    Returns:
        True when the name refers to the AI package.
    """
    return name == "lintro.ai" or name.startswith("lintro.ai.")


def _imports_ai(node: ast.AST) -> bool:
    """Whether an AST node is a runtime import of :mod:`lintro.ai`.

    Args:
        node: Any node from the parsed module.

    Returns:
        True when the node imports from the AI package.
    """
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        if _is_ai_module(module):
            return True
        return module == "lintro" and any(alias.name == "ai" for alias in node.names)
    if isinstance(node, ast.Import):
        return any(_is_ai_module(alias.name) for alias in node.names)
    return False


def _guarded_source_files() -> list[Path]:
    """Collect Python source files under the AC10-guarded core packages.

    Returns:
        Sorted list of source paths to scan.
    """
    files: list[Path] = []
    for relative in _GUARDED_RELATIVE_PATHS:
        path = _PACKAGE_ROOT / relative
        if path.is_file():
            files.append(path)
            continue
        files.extend(sorted(path.rglob("*.py")))
    return files


def test_core_configuration_and_execution_packages_never_import_ai() -> None:
    """No runtime import edge from core config/execution packages into AI.

    TYPE_CHECKING-only imports are allowed (annotations); every other import of
    ``lintro.ai`` under the guarded trees is an AC10 violation.
    """
    offenders: list[str] = []

    for source_path in _guarded_source_files():
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        type_only = {
            child
            for node in ast.walk(tree)
            if isinstance(node, ast.If) and _is_type_checking(node.test)
            for statement in node.body
            for child in ast.walk(statement)
        }
        for node in ast.walk(tree):
            if node in type_only:
                continue
            if not _imports_ai(node):
                continue
            line = getattr(node, "lineno", 0)
            offenders.append(f"{source_path.relative_to(_PACKAGE_ROOT)}:{line}")

    assert_that(offenders).described_as(
        "core modules importing lintro.ai (AC10 / #724)",
    ).is_empty()
