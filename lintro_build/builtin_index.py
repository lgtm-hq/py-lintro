"""Generate ``lintro/plugins/_builtin_index.py``.

Importable implementation behind ``scripts/ci/generate-builtin-tool-index.py``.

The builtin tool registry is populated by importing the ``definition`` module
of every per-tool package under ``lintro/tools/`` (#2311). Globbing that tree
at runtime only works when lintro runs from a source tree or a wheel: Nuitka
``--onefile`` binaries (npm and Homebrew channels) ship compiled modules without
materializing the Python source directory, so the glob found nothing and the
registry stayed empty (#2006).

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

_HEADER = '''"""Auto-generated index of builtin tool modules.

Do not edit by hand. Run
``python3 scripts/ci/generate-builtin-tool-index.py`` to regenerate.

Names are ``<package>.<module>`` paths relative to ``lintro.tools``: the
``definition`` module of every per-tool package, and every public module of a
shared package that has none (#2311). Discovery imports them to populate the
tool registry. Shipping the list as code (rather than globbing
``lintro/tools/*/*.py``) keeps builtin discovery working inside frozen Nuitka
onefile binaries, which never materialize the Python source directory (#2006).
"""

from __future__ import annotations

BUILTIN_TOOL_MODULES: tuple[str, ...] = (
'''

_REGISTERING_HEADER = """)

# The per-tool packages that register a tool with the registry (one of their
# modules applies the ``@register_tool`` decorator). Shared helper packages such
# as ``ts_checker`` are imported but contribute no registry entry. The binary
# smoke test uses this to assert a built binary exposes every builtin tool, not
# merely a non-empty set.
REGISTERING_TOOL_PACKAGES: tuple[str, ...] = (
"""

_FOOTER = ")\n"

# Decorator name that marks a tool module as contributing a registry entry.
REGISTER_TOOL_NAME = "register_tool"

# Packages under ``lintro/tools`` that are not per-tool packages: shared
# scaffolding the tool packages import, never a registry entry of its own.
NON_TOOL_PACKAGES: frozenset[str] = frozenset({"core"})

# Module a per-tool package declares its plugin in (#2311).
DEFINITION_MODULE_NAME = "definition"


def resolve_paths(repo_root: Path) -> tuple[Path, Path]:
    """Derive the generator's input and output paths from a repository root.

    The index lives under ``lintro/plugins`` rather than next to the tool
    packages it lists: ``lintro.tools.__init__`` imports the tool manager,
    which imports discovery, so importing anything from ``lintro.tools`` at
    discovery import time would close an import cycle.

    Args:
        repo_root: Repository root directory.

    Returns:
        Tuple of (tools directory, index module path).
    """
    return (
        repo_root / "lintro" / "tools",
        repo_root / "lintro" / "plugins" / "_builtin_index.py",
    )


def _tool_packages(tools_dir: Path) -> list[Path]:
    """Return the per-tool package directories under ``tools_dir``.

    Args:
        tools_dir: The ``lintro/tools`` directory.

    Returns:
        Sorted package directories, excluding private names and the shared
        scaffolding packages named by :data:`NON_TOOL_PACKAGES`.

    Raises:
        FileNotFoundError: When ``tools_dir`` does not exist.
    """
    if not tools_dir.is_dir():
        msg = f"Builtin tools directory not found: {tools_dir}"
        raise FileNotFoundError(msg)

    return sorted(
        path
        for path in tools_dir.iterdir()
        if path.is_dir()
        and not path.name.startswith((".", "_"))
        and path.name not in NON_TOOL_PACKAGES
        and (path / "__init__.py").is_file()
    )


def _public_modules(package: Path) -> list[Path]:
    """Return the public module files of one per-tool package.

    Args:
        package: A per-tool package directory.

    Returns:
        Sorted ``*.py`` files, excluding ``__init__.py`` and private modules.
    """
    return sorted(
        path for path in package.glob("*.py") if not path.name.startswith("_")
    )


def _entry_modules(package: Path) -> list[Path]:
    """Return the modules discovery must import for one package.

    A per-tool package is entered through its ``definition`` module: importing
    it runs the package ``__init__``, which is the package's re-export surface,
    so every other module the tool needs comes along. Listing the rest here
    would defeat the deliberate laziness of packages that keep a heavy module
    (``idiom_review.engine``, which reaches into :mod:`lintro.ai`) out of the
    import surface. A shared package with no ``definition`` module — the
    ``ts_checker`` family behind ``tsc`` and ``vue-tsc`` — has no such entry
    point, so all of its public modules are listed instead.

    Args:
        package: A per-tool package directory.

    Returns:
        Sorted module files discovery should import for this package.
    """
    definition = package / f"{DEFINITION_MODULE_NAME}.py"
    if definition.is_file():
        return [definition]
    return _public_modules(package)


def collect_module_names(tools_dir: Path) -> list[str]:
    """Collect the builtin tool module names from the source tree.

    Args:
        tools_dir: The ``lintro/tools`` directory holding the per-tool packages.

    Returns:
        Sorted ``<package>.<module>`` names relative to ``lintro.tools``: each
        package's ``definition`` module, or every public module of a shared
        package that has none.
    """
    return sorted(
        f"{package.name}.{module.stem}"
        for package in _tool_packages(tools_dir)
        for module in _entry_modules(package)
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
    """Return whether Python source contributes a registry entry.

    True when the module applies ``@register_tool``. Parsed with :mod:`ast` so
    comments and string literals cannot count as a registration. The generator
    stays stdlib-only: importing the registry at generation time would pull the
    ``lintro`` package (and its import cycle with ``lintro.tools``) into
    minimal CI containers.

    Args:
        source: Module source text.
        path: Path of the file, used in parse-error messages.

    Returns:
        True when the module registers a tool.

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


def collect_registering_package_names(tools_dir: Path) -> list[str]:
    """Collect the per-tool packages that contribute a registry entry.

    Registration is detected by walking each module's AST for a
    ``register_tool`` decorator (a ``Name`` or ``Attribute``). Comments and
    docstrings that mention the decorator do not count. A shared package such
    as ``ts_checker`` registers nothing and is therefore absent.

    Args:
        tools_dir: The ``lintro/tools`` directory holding the per-tool packages.

    Returns:
        Sorted package names holding a module that applies ``@register_tool``.
    """
    registering: list[str] = []
    for package in _tool_packages(tools_dir):
        if any(
            _source_registers_tool(
                source=module.read_text(encoding="utf-8"),
                path=module,
            )
            for module in _public_modules(package)
        ):
            registering.append(package.name)
    return sorted(registering)


def render_index(module_names: list[str], registering: list[str]) -> str:
    """Render the text of the generated index module.

    Args:
        module_names: Sorted ``<package>.<module>`` names under
            ``lintro.tools``.
        registering: Sorted per-tool package names that register a tool.

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
    tools_dir: Path,
    index_path: Path,
) -> int:
    """Run the generator.

    Args:
        argv: Optional argv override (for tests and callers).
        tools_dir: The ``lintro/tools`` directory holding the per-tool packages.
        index_path: Path of the generated index module.

    Returns:
        Process exit code: ``0`` on success, ``1`` on drift in ``--check``
        mode, ``2`` when inputs could not be read.
    """
    parser = argparse.ArgumentParser(
        description="Generate the builtin tool module index.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report drift instead of writing; exit 1 when out of sync.",
    )
    args = parser.parse_args(argv)

    try:
        module_names = collect_module_names(tools_dir)
        registering = collect_registering_package_names(tools_dir)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    if not module_names:
        print(
            f"error: no builtin tool modules found in {tools_dir}",
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
