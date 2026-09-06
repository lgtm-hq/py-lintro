"""Re-export shim for the oxlint tool definition (#2311).

The oxlint plugin now lives in its own package,
:mod:`lintro.tools.oxlint`. Plugin discovery still scans this package, so
importing this module registers the tool. Deleted once discovery moves
to the per-tool packages.
"""

from lintro.tools.oxlint.definition import (
    OXLINT_DEFAULT_PRIORITY,
    OXLINT_DEFAULT_TIMEOUT,
    OXLINT_FILE_PATTERNS,
    OxlintPlugin,
)

__all__ = [
    "OXLINT_DEFAULT_PRIORITY",
    "OXLINT_DEFAULT_TIMEOUT",
    "OXLINT_FILE_PATTERNS",
    "OxlintPlugin",
]
