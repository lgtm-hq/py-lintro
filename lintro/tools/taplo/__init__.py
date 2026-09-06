"""Taplo tool package.

Everything the ``taplo`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.taplo.definition`. Plugin discovery enters the package
through that module (#2311).
"""

from lintro.tools.taplo.definition import (
    TAPLO_DEFAULT_PRIORITY,
    TAPLO_DEFAULT_TIMEOUT,
    TAPLO_FILE_PATTERNS,
    TaploPlugin,
)

__all__ = [
    "TAPLO_DEFAULT_PRIORITY",
    "TAPLO_DEFAULT_TIMEOUT",
    "TAPLO_FILE_PATTERNS",
    "TaploPlugin",
]
