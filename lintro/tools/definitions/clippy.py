"""Re-export shim for the clippy tool definition (#2311).

The clippy plugin now lives in its own package, :mod:`lintro.tools.clippy`.
Plugin discovery still scans this package, so importing this module registers
the tool. Deleted once discovery moves to the per-tool packages.
"""

from lintro.tools.clippy.definition import (
    CLIPPY_DEFAULT_PRIORITY,
    CLIPPY_DEFAULT_TIMEOUT,
    CLIPPY_FILE_PATTERNS,
    ClippyPlugin,
)

__all__ = [
    "CLIPPY_DEFAULT_PRIORITY",
    "CLIPPY_DEFAULT_TIMEOUT",
    "CLIPPY_FILE_PATTERNS",
    "ClippyPlugin",
]
