"""Generate ``lintro/plugins/_builtin_index.py``.

Importable implementation behind ``scripts/ci/generate-builtin-tool-index.py``.

The builtin tool registry is populated by importing every module under
``lintro/tools/definitions/``. Globbing that directory at runtime only works
when lintro runs from a source tree or a wheel: Nuitka ``--onefile`` binaries
(npm and Homebrew channels) ship compiled modules without materializing the
Python source directory, so the glob found nothing and the registry stayed
empty (#2006).

This generator writes the module list into an importable Python module that
travels with the compiled package, making discovery independent of the
filesystem layout.

Modes:
    default: write the index module, exit 0.
    --check: exit 1 with a unified diff if writing would change anything,
             exit 0 when the committed index is already in sync,
             exit 2 on input error.

Stdlib-only on purpose so it runs in any minimal container without installing
dependencies.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import sys
from pathlib import Path

from .exit_codes import EXIT_DRIFT, EXIT_INPUT_ERROR, EXIT_OK

_HEADER = '''"""Auto-generated index of builtin tool definition modules.

Do not edit by hand. Run
``python3 scripts/ci/generate-builtin-tool-index.py`` to regenerate.

Names are module base names under ``lintro/tools/definitions/``. Discovery
imports them to populate the tool registry. Shipping the list as code (rather
than globbing ``lintro/tools/definitions/*.py``) keeps builtin discovery
working inside frozen Nuitka onefile binaries, which never materialize the
Python source directory (#2006).
"""

from __future__ import annotations

BUILTIN_TOOL_MODULES: tuple[str, ...] = (
'''

_REGISTERING_HEADER = """)

# Subset of the modules above that register a tool with the registry (they use
# the ``@register_tool`` decorator). Helper modules that only support a tool are
# imported but contribute no registry entry. The binary smoke test uses this to
# assert a built binary exposes every builtin tool, not merely a non-empty set.
REGISTERING_TOOL_MODULES: tuple[str, ...] = (
"""

_FOOTER = ")\n"

# Decorator name that marks a definition module as contributing a registry entry.
REGISTER_TOOL_NAME = "register_tool"

# Import prefix and module suffix of a per-tool package's plugin module, e.g.
# ``lintro.tools.ruff.definition`` (#2311). A definition module that is only a
# re-export shim for one of those still contributes a registry entry, because
# importing it imports the package module that applies ``@register_tool``.
PER_TOOL_PACKAGE_PREFIX = "lintro.tools."
DEFINITION_MODULE_SUFFIX = ".definition"


def resolve_paths(repo_root: Path) -> tuple[Path, Path]:
    """Derive the generator's input and output paths from a repository root.

    The index lives under ``lintro/plugins`` rather than next to the
    definitions it lists: ``lintro.tools.__init__`` imports the tool manager,
    which imports discovery, so importing anything from ``lintro.tools`` at
    discovery import time would close an import cycle.

    Args:
        repo_root: Repository root directory.

    Returns:
        Tuple of (definitions directory, index module path).
    """
    return (
        repo_root / "lintro" / "tools" / "definitions",
        repo_root / "lintro" / "plugins" / "_builtin_index.py",
    )


def collect_module_names(definitions_dir: Path) -> list[str]:
    """Collect the builtin definition module names from the source tree.

    Args:
        definitions_dir: Directory holding the builtin tool definition modules.

    Returns:
        Sorted module base names, excluding private/dunder modules.

    Raises:
        FileNotFoundError: When ``definitions_dir`` does not exist.
    """
    if not definitions_dir.is_dir():
        msg = f"Builtin definitions directory not found: {definitions_dir}"
        raise FileNotFoundError(msg)

    return sorted(
        path.stem
        for path in definitions_dir.glob("*.py")
        if not path.name.startswith("_")
    )


def _is_register_tool_decorator(node: ast.AST) -> bool:
    """Return whether ``node`` is a ``register_tool`` decorator expression.

    Accepts ``@register_tool``, ``@register_tool()``, and
    ``@module.register_tool`` (a ``Name`` or ``Attribute``, optionally called).

    Args:
        node: A decorator AST node from a ``decorator_list``.

    Returns:
        True when the decorator applies ``register_tool``.
    """
    if isinstance(node, ast.Call):
        return _is_register_tool_decorator(node.func)
    if isinstance(node, ast.Name):
        return node.id == REGISTER_TOOL_NAME
    if isinstance(node, ast.Attribute):
        return node.attr == REGISTER_TOOL_NAME
    return False


def _is_definition_reexport(node: ast.AST) -> bool:
    """Return whether ``node`` imports from a per-tool package's plugin module.

    ``from lintro.tools.ruff.definition import RuffPlugin`` is the re-export
    shim #2311 leaves in ``lintro/tools/definitions`` when a tool moves into
    its own package. Importing the shim imports that module, so the shim still
    contributes a registry entry.

    Args:
        node: An AST node from the module being inspected.

    Returns:
        True when the node is such an import.
    """
    if not isinstance(node, ast.ImportFrom) or node.module is None:
        return False
    return node.module.startswith(PER_TOOL_PACKAGE_PREFIX) and node.module.endswith(
        DEFINITION_MODULE_SUFFIX,
    )


def _source_registers_tool(*, source: str, path: Path) -> bool:
    """Return whether Python source contributes a registry entry.

    True when the module applies ``@register_tool`` itself, and also when it is
    a re-export shim for a per-tool package's ``definition`` module, which
    applies the decorator on its behalf. Parsed with :mod:`ast` so comments and
    string literals cannot count as a registration. The generator stays
    stdlib-only: importing the registry at generation time would pull the
    ``lintro`` package (and its import cycle with ``lintro.tools``) into
    minimal CI containers.

    Args:
        source: Module source text.
        path: Path of the file, used in parse-error messages.

    Returns:
        True when the module registers a tool directly or re-exports one.

    Raises:
        ValueError: When ``source`` is not valid Python.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        msg = f"could not parse {path}: {exc}"
        raise ValueError(msg) from exc

    for node in ast.walk(tree):
        if _is_definition_reexport(node):
            return True
        decorator_list = getattr(node, "decorator_list", None)
        if not decorator_list:
            continue
        if any(_is_register_tool_decorator(dec) for dec in decorator_list):
            return True
    return False


def collect_registering_module_names(definitions_dir: Path) -> list[str]:
    """Collect the definition modules that contribute a registry entry.

    Registration is detected by walking each module's AST for a
    ``register_tool`` decorator (a ``Name`` or ``Attribute``), or for an import
    from a per-tool package's ``definition`` module, which is what a re-export
    shim carries instead (#2311). Comments and docstrings that mention the
    decorator do not count.

    Args:
        definitions_dir: Directory holding the builtin tool definition modules.

    Returns:
        Sorted module base names whose source applies ``@register_tool`` or
        re-exports a per-tool package's plugin.

    Raises:
        FileNotFoundError: When ``definitions_dir`` does not exist.
        ValueError: When a definition file cannot be parsed as Python.
    """
    if not definitions_dir.is_dir():
        msg = f"Builtin definitions directory not found: {definitions_dir}"
        raise FileNotFoundError(msg)

    registering: list[str] = []
    for path in definitions_dir.glob("*.py"):
        if path.name.startswith("_"):
            continue
        try:
            registers = _source_registers_tool(
                source=path.read_text(encoding="utf-8"),
                path=path,
            )
        except ValueError:
            raise
        if registers:
            registering.append(path.stem)
    return sorted(registering)


def render_index(module_names: list[str], registering: list[str]) -> str:
    """Render the text of the generated index module.

    Args:
        module_names: Sorted builtin definition module names.
        registering: Sorted subset that registers a tool.

    Returns:
        Full module source text, formatted the way black would emit it.
    """
    modules_body = "".join(f'    "{name}",\n' for name in module_names)
    registering_body = "".join(f'    "{name}",\n' for name in registering)
    return f"{_HEADER}{modules_body}{_REGISTERING_HEADER}{registering_body}{_FOOTER}"


def _report_drift(*, current: str, desired: str, path: Path) -> None:
    """Print a unified diff describing the drift for a generated file.

    Args:
        current: Text currently committed at ``path``.
        desired: Text the generator would write.
        path: Path of the generated file.
    """
    diff = difflib.unified_diff(
        current.splitlines(keepends=True),
        desired.splitlines(keepends=True),
        fromfile=f"{path} (committed)",
        tofile=f"{path} (generated)",
    )
    sys.stdout.writelines(diff)


def main(
    argv: list[str] | None = None,
    *,
    definitions_dir: Path,
    index_path: Path,
) -> int:
    """Run the generator.

    Args:
        argv: Optional argv override (for tests and callers).
        definitions_dir: Directory holding the builtin tool definition modules.
        index_path: Path of the generated index module.

    Returns:
        Process exit code: ``0`` on success, ``1`` on drift in ``--check``
        mode, ``2`` when inputs could not be read.
    """
    parser = argparse.ArgumentParser(
        description="Generate the builtin tool definition module index.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report drift instead of writing; exit 1 when out of sync.",
    )
    args = parser.parse_args(argv)

    try:
        module_names = collect_module_names(definitions_dir)
        registering = collect_registering_module_names(definitions_dir)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    if not module_names:
        print(
            f"error: no builtin tool definitions found in {definitions_dir}",
            file=sys.stderr,
        )
        return EXIT_INPUT_ERROR

    desired = render_index(module_names, registering)
    current = index_path.read_text(encoding="utf-8") if index_path.exists() else ""

    if args.check:
        if current == desired:
            print(f"{index_path} is up to date ({len(module_names)} modules)")
            return EXIT_OK
        print(f"error: {index_path} is out of date; regenerate it with:")
        print("  python3 scripts/ci/generate-builtin-tool-index.py")
        _report_drift(current=current, desired=desired, path=index_path)
        return EXIT_DRIFT

    if current != desired:
        index_path.write_text(desired, encoding="utf-8")
        print(f"wrote {index_path} ({len(module_names)} modules)")
    else:
        print(f"{index_path} already up to date ({len(module_names)} modules)")
    return EXIT_OK
