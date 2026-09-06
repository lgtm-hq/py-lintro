"""Re-export shim for the trufflehog tool definition (#2311).

The trufflehog plugin now lives in its own package,
:mod:`lintro.tools.trufflehog`. Plugin discovery still scans this package, so
importing this module registers the tool. Deleted once discovery moves
to the per-tool packages.
"""

from lintro.tools.trufflehog.definition import (
    TRUFFLEHOG_DEFAULT_PRIORITY,
    TRUFFLEHOG_DEFAULT_TIMEOUT,
    TRUFFLEHOG_FILE_PATTERNS,
    TrufflehogPlugin,
)

__all__ = [
    "TRUFFLEHOG_DEFAULT_PRIORITY",
    "TRUFFLEHOG_DEFAULT_TIMEOUT",
    "TRUFFLEHOG_FILE_PATTERNS",
    "TrufflehogPlugin",
]
