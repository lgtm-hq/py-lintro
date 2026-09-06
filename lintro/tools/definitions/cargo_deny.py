"""Re-export shim for the cargo-deny tool definition (#2311).

The cargo-deny plugin now lives in its own package, :mod:`lintro.tools.cargo_deny`.
Plugin discovery still scans this package, so importing this module registers
the tool. Deleted once discovery moves to the per-tool packages.
"""

from lintro.tools.cargo_deny.definition import (
    CARGO_DENY_DEFAULT_PRIORITY,
    CARGO_DENY_DEFAULT_TIMEOUT,
    CARGO_DENY_FILE_PATTERNS,
    CargoDenyPlugin,
)

__all__ = [
    "CARGO_DENY_DEFAULT_PRIORITY",
    "CARGO_DENY_DEFAULT_TIMEOUT",
    "CARGO_DENY_FILE_PATTERNS",
    "CargoDenyPlugin",
]
