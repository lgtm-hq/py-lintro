"""Re-export shim for the markdownlint tool definition (#2311).

The markdownlint plugin now lives in its own package,
:mod:`lintro.tools.markdownlint`. Plugin discovery still scans this package, so
importing this module registers the tool. Deleted once discovery moves
to the per-tool packages.
"""

from lintro.tools.markdownlint.definition import (
    MARKDOWNLINT_DEFAULT_PRIORITY,
    MARKDOWNLINT_DEFAULT_TIMEOUT,
    MARKDOWNLINT_FILE_PATTERNS,
    MarkdownlintPlugin,
)

__all__ = [
    "MARKDOWNLINT_DEFAULT_PRIORITY",
    "MARKDOWNLINT_DEFAULT_TIMEOUT",
    "MARKDOWNLINT_FILE_PATTERNS",
    "MarkdownlintPlugin",
]
