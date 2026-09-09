#!/usr/bin/env python3
"""Nuitka user plugin: ship chosen packages as bytecode instead of C (#2484).

A ``--generate-c-only`` audit of the binary closure found ``pygments``
contributing 321 compiled C units, 260 of them individual language lexers,
for a single ``rich.syntax`` call site. That is roughly a quarter of the C
compile for code that is dispatched dynamically at runtime and gains nothing
measurable from compilation.

Nuitka already ships ``rich`` itself as bytecode for the same reason, via the
``decideCompilation`` hook its ``ImplicitImports`` plugin implements against a
hard-coded "not performance relevant" namespace list. That list is not
reachable from the command line, so this plugin uses the same hook to extend
it. Bytecode modules are still fully present in the binary and still import
normally, including the lexers ``pygments`` loads by name at runtime -
``scripts/build/verify_built_binary.sh`` exercises exactly that path.

The selection logic lives in :func:`is_bytecode_namespace`, a plain string
function with no Nuitka dependency, so it stays testable in the CI test jobs -
which install lintro's dev group but not the ``build`` group Nuitka lives in.
The plugin class is what Nuitka itself loads, inside a process where Nuitka is
by definition importable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nuitka.plugins.PluginBase import NuitkaPluginBase
else:
    try:
        from nuitka.plugins.PluginBase import NuitkaPluginBase
    except ImportError:
        # Importing this module without Nuitka installed is expected: the unit
        # tests do it to exercise the selection logic. Nuitka loads it through
        # `--user-plugin` in a process where the real base class is present.
        NuitkaPluginBase = object

#: Package namespaces included as bytecode rather than compiled to C.
BYTECODE_NAMESPACES: tuple[str, ...] = ("pygments",)


def is_bytecode_namespace(
    module_name: str,
    *,
    namespaces: tuple[str, ...] = BYTECODE_NAMESPACES,
) -> bool:
    """Report whether a module belongs to one of the bytecode namespaces.

    Matching is on dotted namespace boundaries, so ``pygments.lexers`` matches
    ``pygments`` while ``pygmentsfoo`` does not.

    Args:
        module_name: Dotted module name under consideration.
        namespaces: Namespaces to ship as bytecode.

    Returns:
        ``True`` when the module is in one of ``namespaces``.
    """
    return any(
        module_name == namespace or module_name.startswith(f"{namespace}.")
        for namespace in namespaces
    )


class NuitkaPluginLintroBytecode(NuitkaPluginBase):
    """Include the configured namespaces as bytecode."""

    plugin_name = "lintro-bytecode"
    plugin_desc = "Ship dynamically dispatched packages as bytecode, not C."

    @staticmethod
    def isAlwaysEnabled() -> bool:  # noqa: N802 - Nuitka's hook name
        """Report that the plugin needs no explicit enabling.

        Returns:
            Always ``True``; the plugin is loaded via ``--user-plugin``.
        """
        return True

    def decideCompilation(self, module_name: Any) -> str | None:  # noqa: N802
        """Choose bytecode for the configured namespaces.

        Args:
            module_name: The Nuitka ``ModuleName`` under consideration; only
                its string form is used, so a plain ``str`` works too.

        Returns:
            ``"bytecode"`` for a configured namespace, else ``None`` to leave
            the decision to the other plugins.
        """
        if is_bytecode_namespace(str(module_name)):
            return "bytecode"
        return None
