"""Re-export shim for the osv_scanner tool definition (#2311).

The osv_scanner plugin now lives in its own package,
:mod:`lintro.tools.osv_scanner`. Plugin discovery still scans this package, so
importing this module registers the tool. Deleted once discovery moves
to the per-tool packages.
"""

from lintro.tools.osv_scanner.definition import (
    OSV_SCANNER_DEFAULT_PRIORITY,
    OSV_SCANNER_DEFAULT_TIMEOUT,
    OsvScannerPlugin,
)

__all__ = [
    "OSV_SCANNER_DEFAULT_PRIORITY",
    "OSV_SCANNER_DEFAULT_TIMEOUT",
    "OsvScannerPlugin",
]
