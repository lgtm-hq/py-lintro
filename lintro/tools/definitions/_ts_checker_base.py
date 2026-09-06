"""Re-export shim for the shared TypeScript-checker base (#2311).

The base class now lives in :mod:`lintro.tools.ts_checker.base`, next to the
``tsc`` and ``vue-tsc`` packages that subclass it. Deleted once discovery
moves to the per-tool packages.
"""

from lintro.tools.ts_checker.base import TypeScriptCheckerPlugin

__all__ = [
    "TypeScriptCheckerPlugin",
]
