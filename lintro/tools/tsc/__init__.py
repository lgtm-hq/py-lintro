"""Tsc tool package.

Everything the ``tsc`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.tsc.definition`. Plugin discovery enters the package
through that module (#2311).
"""

from lintro.tools.tsc.definition import (
    FRAMEWORK_CONFIGS,
    TSC_DEFAULT_PRIORITY,
    TSC_DEFAULT_TIMEOUT,
    TSC_FILE_PATTERNS,
    TscPlugin,
)

__all__ = [
    "FRAMEWORK_CONFIGS",
    "TSC_DEFAULT_PRIORITY",
    "TSC_DEFAULT_TIMEOUT",
    "TSC_FILE_PATTERNS",
    "TscPlugin",
]
