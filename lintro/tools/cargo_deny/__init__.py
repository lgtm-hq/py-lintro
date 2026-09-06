"""Cargo-deny tool package.

Everything the ``cargo-deny`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.cargo_deny.definition`. Plugin discovery enters the package
through that module (#2311).
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
