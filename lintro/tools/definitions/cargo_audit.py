"""Re-export shim for the cargo-audit tool definition (#2311).

The cargo-audit plugin now lives in its own package, :mod:`lintro.tools.cargo_audit`.
Plugin discovery still scans this package, so importing this module registers
the tool. Deleted once discovery moves to the per-tool packages.
"""

from lintro.tools.cargo_audit.definition import (
    CARGO_AUDIT_DEFAULT_PRIORITY,
    CARGO_AUDIT_DEFAULT_TIMEOUT,
    CARGO_AUDIT_FILE_PATTERNS,
    CargoAuditPlugin,
)

__all__ = [
    "CARGO_AUDIT_DEFAULT_PRIORITY",
    "CARGO_AUDIT_DEFAULT_TIMEOUT",
    "CARGO_AUDIT_FILE_PATTERNS",
    "CargoAuditPlugin",
]
