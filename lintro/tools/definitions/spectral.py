"""Re-export shim for the spectral tool definition (#2311).

The spectral plugin now lives in its own package,
:mod:`lintro.tools.spectral`. Plugin discovery still scans this package, so
importing this module registers the tool. Deleted once discovery moves
to the per-tool packages.
"""

from lintro.tools.spectral.definition import (
    SPECTRAL_DEFAULT_PRIORITY,
    SPECTRAL_DEFAULT_TIMEOUT,
    SPECTRAL_FILE_PATTERNS,
    SPECTRAL_RULESET_FILES,
    SpectralPlugin,
)

__all__ = [
    "SPECTRAL_DEFAULT_PRIORITY",
    "SPECTRAL_DEFAULT_TIMEOUT",
    "SPECTRAL_FILE_PATTERNS",
    "SPECTRAL_RULESET_FILES",
    "SpectralPlugin",
]
