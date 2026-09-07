"""Re-export shim for the vale tool definition (#2311).

The vale plugin now lives in its own package,
:mod:`lintro.tools.vale`. Plugin discovery still scans this package, so
importing this module registers the tool. Deleted once discovery moves
to the per-tool packages.
"""

from lintro.tools.vale.definition import (
    VALE_CONFIG_FILENAMES,
    VALE_DEFAULT_PRIORITY,
    VALE_DEFAULT_TIMEOUT,
    VALE_FILE_PATTERNS,
    ValePlugin,
)

__all__ = [
    "VALE_CONFIG_FILENAMES",
    "VALE_DEFAULT_PRIORITY",
    "VALE_DEFAULT_TIMEOUT",
    "VALE_FILE_PATTERNS",
    "ValePlugin",
]
