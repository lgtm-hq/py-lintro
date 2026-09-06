"""Re-export shim for the pydoclint tool definition (#2311).

The pydoclint plugin now lives in its own package,
:mod:`lintro.tools.pydoclint`. Plugin discovery still scans this package, so
importing this module registers the tool. Deleted once discovery moves
to the per-tool packages.
"""

from lintro.tools.pydoclint.definition import (
    PYDOCLINT_DEFAULT_PRIORITY,
    PYDOCLINT_DEFAULT_TIMEOUT,
    PYDOCLINT_FILE_PATTERNS,
    PydoclintPlugin,
)

__all__ = [
    "PYDOCLINT_DEFAULT_PRIORITY",
    "PYDOCLINT_DEFAULT_TIMEOUT",
    "PYDOCLINT_FILE_PATTERNS",
    "PydoclintPlugin",
]
