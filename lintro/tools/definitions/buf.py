"""Re-export shim for the buf tool definition (#2311).

The buf plugin now lives in its own package, :mod:`lintro.tools.buf`.
Plugin discovery still scans this package, so importing this module registers
the tool. Deleted once discovery moves to the per-tool packages.
"""

from lintro.tools.buf.definition import (
    BUF_DEFAULT_PRIORITY,
    BUF_DEFAULT_TIMEOUT,
    BUF_FILE_PATTERNS,
    BufPlugin,
)

__all__ = [
    "BUF_DEFAULT_PRIORITY",
    "BUF_DEFAULT_TIMEOUT",
    "BUF_FILE_PATTERNS",
    "BufPlugin",
]
