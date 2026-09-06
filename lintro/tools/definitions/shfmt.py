"""Re-export shim for the shfmt tool definition (#2311).

The shfmt plugin now lives in its own package,
:mod:`lintro.tools.shfmt`. Plugin discovery still scans this package, so
importing this module registers the tool. Deleted once discovery moves
to the per-tool packages.
"""

from lintro.tools.shfmt.definition import (
    SHFMT_DEFAULT_PRIORITY,
    SHFMT_DEFAULT_TIMEOUT,
    SHFMT_FILE_PATTERNS,
    ShfmtPlugin,
)

__all__ = [
    "SHFMT_DEFAULT_PRIORITY",
    "SHFMT_DEFAULT_TIMEOUT",
    "SHFMT_FILE_PATTERNS",
    "ShfmtPlugin",
]
