"""Re-export shim for the rustfmt tool definition (#2311).

The rustfmt plugin now lives in its own package,
:mod:`lintro.tools.rustfmt`. Plugin discovery still scans this package, so
importing this module registers the tool. Deleted once discovery moves
to the per-tool packages.
"""

from lintro.tools.rustfmt.definition import (
    RUSTFMT_DEFAULT_PRIORITY,
    RUSTFMT_DEFAULT_TIMEOUT,
    RUSTFMT_FILE_PATTERNS,
    RustfmtPlugin,
)

__all__ = [
    "RUSTFMT_DEFAULT_PRIORITY",
    "RUSTFMT_DEFAULT_TIMEOUT",
    "RUSTFMT_FILE_PATTERNS",
    "RustfmtPlugin",
]
