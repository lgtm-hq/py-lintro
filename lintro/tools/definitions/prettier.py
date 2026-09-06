"""Re-export shim for the prettier tool definition (#2311).

The prettier plugin now lives in its own package,
:mod:`lintro.tools.prettier`. Plugin discovery still scans this package, so
importing this module registers the tool. Deleted once discovery moves
to the per-tool packages.
"""

from lintro.tools.prettier.definition import (
    PRETTIER_CONFIG_FILENAMES,
    PRETTIER_DEFAULT_PRIORITY,
    PRETTIER_DEFAULT_TIMEOUT,
    PRETTIER_FILE_PATTERNS,
    PrettierPlugin,
)

__all__ = [
    "PRETTIER_CONFIG_FILENAMES",
    "PRETTIER_DEFAULT_PRIORITY",
    "PRETTIER_DEFAULT_TIMEOUT",
    "PRETTIER_FILE_PATTERNS",
    "PrettierPlugin",
]
