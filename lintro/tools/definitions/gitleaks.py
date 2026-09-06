"""Re-export shim for the gitleaks tool definition (#2311).

The gitleaks plugin now lives in its own package, :mod:`lintro.tools.gitleaks`.
Plugin discovery still scans this package, so importing this module registers
the tool. Deleted once discovery moves to the per-tool packages.
"""

from lintro.tools.gitleaks.definition import (
    GITLEAKS_DEFAULT_PRIORITY,
    GITLEAKS_DEFAULT_TIMEOUT,
    GITLEAKS_FILE_PATTERNS,
    GITLEAKS_OUTPUT_FORMAT,
    GitleaksPlugin,
)

__all__ = [
    "GITLEAKS_DEFAULT_PRIORITY",
    "GITLEAKS_DEFAULT_TIMEOUT",
    "GITLEAKS_FILE_PATTERNS",
    "GITLEAKS_OUTPUT_FORMAT",
    "GitleaksPlugin",
]
