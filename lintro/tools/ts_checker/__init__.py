"""Shared TypeScript type-checker package.

``tsc`` and ``vue-tsc`` drive the same compiler with near-identical
orchestration, so their common shape lives here rather than in either tool's
package: :mod:`lintro.tools.ts_checker.base` holds the plugin base class,
:mod:`lintro.tools.ts_checker.command` the command and tsconfig helpers, and
:mod:`lintro.tools.ts_checker.execution` the check orchestration. The package
declares no plugin, so it registers no tool; discovery lists all of its public
modules rather than a single ``definition`` entry point (#2311).
"""

from lintro.tools.ts_checker.base import TypeScriptCheckerPlugin

__all__ = [
    "TypeScriptCheckerPlugin",
]
