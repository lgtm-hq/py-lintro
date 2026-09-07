"""Typos tool package.

Everything the ``typos`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.typos.definition`. Plugin discovery enters the package
through that module (#2311).
"""

from lintro.tools.typos.definition import (
    BINARY_PATH_SUFFIXES,
    BINARY_SNIFF_BYTES,
    TYPOS_CONFIG_FILENAMES,
    TYPOS_DEFAULT_FORMAT,
    TYPOS_DEFAULT_PRIORITY,
    TYPOS_DEFAULT_TIMEOUT,
    TYPOS_FILE_PATTERNS,
    TYPOS_ISSUES_EXIT_CODE,
    TyposPlugin,
)

__all__ = [
    "BINARY_PATH_SUFFIXES",
    "BINARY_SNIFF_BYTES",
    "TYPOS_CONFIG_FILENAMES",
    "TYPOS_DEFAULT_FORMAT",
    "TYPOS_DEFAULT_PRIORITY",
    "TYPOS_DEFAULT_TIMEOUT",
    "TYPOS_FILE_PATTERNS",
    "TYPOS_ISSUES_EXIT_CODE",
    "TyposPlugin",
]
