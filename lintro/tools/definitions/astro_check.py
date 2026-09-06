"""Re-export shim for the astro-check tool definition (#2311).

The astro-check plugin now lives in its own package, :mod:`lintro.tools.astro_check`.
Plugin discovery still scans this package, so importing this module registers
the tool. Deleted once discovery moves to the per-tool packages.
"""

from lintro.tools.astro_check.definition import (
    ASTRO_CHECK_DEFAULT_PRIORITY,
    ASTRO_CHECK_DEFAULT_TIMEOUT,
    ASTRO_CHECK_FILE_PATTERNS,
    ASTRO_CHECK_OPTION_TYPES,
    AstroCheckPlugin,
)

__all__ = [
    "ASTRO_CHECK_DEFAULT_PRIORITY",
    "ASTRO_CHECK_DEFAULT_TIMEOUT",
    "ASTRO_CHECK_FILE_PATTERNS",
    "ASTRO_CHECK_OPTION_TYPES",
    "AstroCheckPlugin",
]
