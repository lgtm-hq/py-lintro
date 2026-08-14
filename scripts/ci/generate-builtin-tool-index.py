#!/usr/bin/env python3
"""Generate ``lintro/plugins/_builtin_index.py``.

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

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_INPUT_ERROR = 2

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFINITIONS_DIR = REPO_ROOT / "lintro" / "tools" / "definitions"

# The index lives under ``lintro/plugins`` rather than next to the definitions
# it lists: ``lintro.tools.__init__`` imports the tool manager, which imports
# discovery, so importing anything from ``lintro.tools`` at discovery import
# time would close an import cycle.
INDEX_PATH = REPO_ROOT / "lintro" / "plugins" / "_builtin_index.py"

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


def _source_registers_tool(*, source: str, path: Path) -> bool:
    """Return whether Python source applies ``@register_tool``.

    Parsed with :mod:`ast` so comments and string literals cannot count as a
    registration. The generator stays stdlib-only: importing the registry at
    generation time would pull the ``lintro`` package (and its import cycle
    with ``lintro.tools``) into minimal CI containers.

    Args:
        source: Module source text.
        path: Path of the file, used in parse-error messages.

    Returns:
        True when a class or function in the module is decorated with
        ``register_tool``.

    Raises:
        ValueError: When ``source`` is not valid Python.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        msg = f"could not parse {path}: {exc}"
        raise ValueError(msg) from exc

    for node in ast.walk(tree):
        decorator_list = getattr(node, "decorator_list", None)
        if not decorator_list:
            continue
        if any(_is_register_tool_decorator(dec) for dec in decorator_list):
            return True
    return False


def collect_registering_module_names(definitions_dir: Path) -> list[str]:
    """Collect the definition modules that register a tool.

    Registration is detected by walking each module's AST for a
    ``register_tool`` decorator (a ``Name`` or ``Attribute``). Comments and
    docstrings that mention the decorator do not count.

    Args:
        definitions_dir: Directory holding the builtin tool definition modules.

    Returns:
        Sorted module base names whose source applies ``@register_tool``.

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


def main() -> int:
    """Run the generator.

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
    args = parser.parse_args()

    try:
        module_names = collect_module_names(DEFINITIONS_DIR)
        registering = collect_registering_module_names(DEFINITIONS_DIR)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    if not module_names:
        print(
            f"error: no builtin tool definitions found in {DEFINITIONS_DIR}",
            file=sys.stderr,
        )
        return EXIT_INPUT_ERROR

    desired = render_index(module_names, registering)
    current = INDEX_PATH.read_text(encoding="utf-8") if INDEX_PATH.exists() else ""

    if args.check:
        if current == desired:
            print(f"{INDEX_PATH} is up to date ({len(module_names)} modules)")
            return EXIT_OK
        print(f"error: {INDEX_PATH} is out of date; regenerate it with:")
        print("  python3 scripts/ci/generate-builtin-tool-index.py")
        _report_drift(current=current, desired=desired, path=INDEX_PATH)
        return EXIT_DRIFT

    if current != desired:
        INDEX_PATH.write_text(desired, encoding="utf-8")
        print(f"wrote {INDEX_PATH} ({len(module_names)} modules)")
    else:
        print(f"{INDEX_PATH} already up to date ({len(module_names)} modules)")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
