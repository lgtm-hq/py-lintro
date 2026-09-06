"""Cargo-audit tool package.

Everything the ``cargo-audit`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.cargo_audit.definition`. Plugin discovery enters the package
through that module (#2311).
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
