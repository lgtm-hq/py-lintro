"""Re-export shim for the taplo tool definition (#2311).

The taplo plugin now lives in its own package,
:mod:`lintro.tools.taplo`. Plugin discovery still scans this package, so
importing this module registers the tool. Deleted once discovery moves
to the per-tool packages.
"""

from lintro.tools.taplo.definition import (
    TAPLO_DEFAULT_PRIORITY,
    TAPLO_DEFAULT_TIMEOUT,
    TAPLO_FILE_PATTERNS,
    TaploPlugin,
)

__all__ = [
    "TAPLO_DEFAULT_PRIORITY",
    "TAPLO_DEFAULT_TIMEOUT",
    "TAPLO_FILE_PATTERNS",
    "TaploPlugin",
]
