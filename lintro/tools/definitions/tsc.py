"""Re-export shim for the tsc tool definition (#2311).

The tsc plugin now lives in its own package,
:mod:`lintro.tools.tsc`. Plugin discovery still scans this package, so
importing this module registers the tool. Deleted once discovery moves
to the per-tool packages.
"""

from lintro.tools.tsc.definition import (
    FRAMEWORK_CONFIGS,
    TSC_DEFAULT_PRIORITY,
    TSC_DEFAULT_TIMEOUT,
    TSC_FILE_PATTERNS,
    TscPlugin,
)

__all__ = [
    "FRAMEWORK_CONFIGS",
    "TSC_DEFAULT_PRIORITY",
    "TSC_DEFAULT_TIMEOUT",
    "TSC_FILE_PATTERNS",
    "TscPlugin",
]
