"""Seed parsing for ``lintro/_tool_packages.py``.

The seed declares which external packages are tools (mapped to a
``ToolName`` member) and which are companions (``None``). The generator
AST-walks the seed file rather than importing it, so this module has no
runtime dependency on lintro itself.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from _generator.errors import GenerationError


@dataclass(frozen=True, slots=True)
class Seed:
    """Parsed seed mapping from ``lintro/_tool_packages.py``.

    Attributes:
        npm_owners: Mapping of npm package name -> ToolName member name or None.
        pypi_owners: Mapping of pypi package name -> ToolName member name or None.
    """

    npm_owners: dict[str, str | None]
    pypi_owners: dict[str, str | None]


def parse_seed(path: Path) -> Seed:
    """Parse the seed mapping without importing lintro.

    AST-walks ``_tool_packages.py`` for the ``NPM_PACKAGE_OWNERS`` and
    ``PYPI_PACKAGE_OWNERS`` assignments and extracts package name to
    ToolName-attribute pairs. ``None`` values are preserved as ``None``.

    Args:
        path: Path to ``lintro/_tool_packages.py``.

    Returns:
        Parsed seed.

    Raises:
        GenerationError: If the seed file is missing or malformed.
    """
    if not path.exists():
        raise GenerationError(f"seed file not found: {path}")

    try:
        tree = ast.parse(path.read_text())
    except SyntaxError as exc:
        raise GenerationError(f"seed file is not valid Python: {exc}") from exc
    npm: dict[str, str | None] | None = None
    pypi: dict[str, str | None] | None = None

    for node in ast.walk(tree):
        target_name = extract_assign_target(node)
        if target_name == "NPM_PACKAGE_OWNERS":
            npm = _extract_owner_mapping(node)
        elif target_name == "PYPI_PACKAGE_OWNERS":
            pypi = _extract_owner_mapping(node)

    if npm is None or pypi is None:
        raise GenerationError(
            "seed must define both NPM_PACKAGE_OWNERS and PYPI_PACKAGE_OWNERS",
        )

    return Seed(npm_owners=npm, pypi_owners=pypi)


def extract_assign_target(node: ast.AST) -> str | None:
    """Return the target name of a top-level dict assignment, else None.

    Handles both annotated (``X: T = ...``) and bare (``X = ...``) forms.

    Args:
        node: The AST node to inspect.

    Returns:
        The target name, or None if the node is not a single-name assignment.
    """
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id
    if (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ):
        return node.targets[0].id
    return None


def _extract_owner_mapping(node: ast.AST) -> dict[str, str | None]:
    """Extract ``{str: ToolName.X | None}`` literal from an Assign/AnnAssign.

    Args:
        node: The Assign or AnnAssign node holding the dict literal.

    Returns:
        Mapping of package name -> ToolName attribute name (or None).

    Raises:
        GenerationError: If the value is not a dict of string keys mapping to
            ``ToolName.X`` attributes or ``None``.
    """
    value = node.value if isinstance(node, ast.AnnAssign | ast.Assign) else None
    if not isinstance(value, ast.Dict):
        raise GenerationError(
            "seed mappings must be dict literals",
        )

    result: dict[str, str | None] = {}
    for key_node, val_node in zip(value.keys, value.values, strict=True):
        if not isinstance(key_node, ast.Constant) or not isinstance(
            key_node.value,
            str,
        ):
            raise GenerationError("seed dict keys must be string literals")
        package = key_node.value

        if isinstance(val_node, ast.Constant) and val_node.value is None:
            result[package] = None
        elif (
            isinstance(val_node, ast.Attribute)
            and isinstance(val_node.value, ast.Name)
            and val_node.value.id == "ToolName"
        ):
            result[package] = val_node.attr
        else:
            raise GenerationError(
                f"seed dict values must be ToolName.X or None "
                f"(got {ast.dump(val_node)})",
            )

    return result
