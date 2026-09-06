"""Re-export shim for the TypeScript-checker command helpers (#2311).

The helpers now live in :mod:`lintro.tools.ts_checker.command`. Deleted once
discovery moves to the per-tool packages.
"""

from lintro.tools.ts_checker.command import doc_url

__all__ = [
    "doc_url",
]
