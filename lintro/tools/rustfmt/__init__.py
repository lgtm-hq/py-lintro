"""Rustfmt tool package.

Everything the ``rustfmt`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.rustfmt.definition`. Plugin discovery enters the package
through that module (#2311).
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
