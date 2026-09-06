"""OSV-Scanner tool package.

Everything the ``osv_scanner`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.osv_scanner.definition`. ``lintro.tools.definitions.osv_scanner``
re-exports the plugin so plugin discovery keeps finding it (#2311).
"""

from lintro.tools.osv_scanner.definition import (
    OSV_SCANNER_DEFAULT_PRIORITY,
    OSV_SCANNER_DEFAULT_TIMEOUT,
    OsvScannerPlugin,
)

__all__ = [
    "OSV_SCANNER_DEFAULT_PRIORITY",
    "OSV_SCANNER_DEFAULT_TIMEOUT",
    "OsvScannerPlugin",
]
