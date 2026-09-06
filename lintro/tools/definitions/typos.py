"""Re-export shim for the typos tool definition (#2311).

The typos plugin now lives in its own package,
:mod:`lintro.tools.typos`. Plugin discovery still scans this package, so
importing this module registers the tool. Deleted once discovery moves
to the per-tool packages.
"""

from lintro.tools.typos.definition import (
    BINARY_PATH_SUFFIXES,
    BINARY_SNIFF_BYTES,
    TYPOS_CONFIG_FILENAMES,
    TYPOS_DEFAULT_FORMAT,
    TYPOS_DEFAULT_PRIORITY,
    TYPOS_DEFAULT_TIMEOUT,
    TYPOS_FILE_PATTERNS,
    TYPOS_ISSUES_EXIT_CODE,
    TyposPlugin,
)

__all__ = [
    "BINARY_PATH_SUFFIXES",
    "BINARY_SNIFF_BYTES",
    "TYPOS_CONFIG_FILENAMES",
    "TYPOS_DEFAULT_FORMAT",
    "TYPOS_DEFAULT_PRIORITY",
    "TYPOS_DEFAULT_TIMEOUT",
    "TYPOS_FILE_PATTERNS",
    "TYPOS_ISSUES_EXIT_CODE",
    "TyposPlugin",
]
