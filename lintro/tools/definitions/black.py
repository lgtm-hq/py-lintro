"""Re-export shim for the black tool definition (#2311).

The black plugin now lives in its own package, :mod:`lintro.tools.black`.
Plugin discovery still scans this package, so importing this module registers
the tool. Deleted once discovery moves to the per-tool packages.
"""

from lintro.tools.black.definition import (
    BLACK_DEFAULT_PRIORITY,
    BLACK_DEFAULT_TIMEOUT,
    BLACK_FILE_PATTERNS,
    BlackPlugin,
)

__all__ = [
    "BLACK_DEFAULT_PRIORITY",
    "BLACK_DEFAULT_TIMEOUT",
    "BLACK_FILE_PATTERNS",
    "BlackPlugin",
]
