"""Re-export shim for the TypeScript-checker execution helpers (#2311).

The helpers now live in :mod:`lintro.tools.ts_checker.execution`. Deleted once
discovery moves to the per-tool packages.
"""

from lintro.tools.ts_checker.execution import check

__all__ = [
    "check",
]
