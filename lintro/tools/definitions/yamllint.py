"""Re-export shim for the yamllint tool definition (#2311).

The yamllint plugin now lives in its own package,
:mod:`lintro.tools.yamllint`. Plugin discovery still scans this package, so
importing this module registers the tool. Deleted once discovery moves
to the per-tool packages.
"""

from lintro.tools.yamllint.definition import (
    YAMLLINT_DEFAULT_PRIORITY,
    YAMLLINT_DEFAULT_TIMEOUT,
    YAMLLINT_FILE_PATTERNS,
    YAMLLINT_FORMATS,
    YamllintPlugin,
)

__all__ = [
    "YAMLLINT_DEFAULT_PRIORITY",
    "YAMLLINT_DEFAULT_TIMEOUT",
    "YAMLLINT_FILE_PATTERNS",
    "YAMLLINT_FORMATS",
    "YamllintPlugin",
]
