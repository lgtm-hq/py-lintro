"""Re-export shim for the oxfmt tool definition (#2311).

The oxfmt plugin now lives in its own package,
:mod:`lintro.tools.oxfmt`. Plugin discovery still scans this package, so
importing this module registers the tool. Deleted once discovery moves
to the per-tool packages.
"""

from lintro.tools.oxfmt.definition import (
    OXFMT_DEFAULT_PRIORITY,
    OXFMT_DEFAULT_TIMEOUT,
    OXFMT_FILE_PATTERNS,
    OxfmtPlugin,
)

__all__ = [
    "OXFMT_DEFAULT_PRIORITY",
    "OXFMT_DEFAULT_TIMEOUT",
    "OXFMT_FILE_PATTERNS",
    "OxfmtPlugin",
]
