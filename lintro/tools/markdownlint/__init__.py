"""Markdownlint tool package.

Everything the ``markdownlint`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.markdownlint.definition`. ``lintro.tools.definitions.markdownlint``
re-exports the plugin so plugin discovery keeps finding it (#2311).
"""

from lintro.tools.markdownlint.definition import (
    MARKDOWNLINT_DEFAULT_PRIORITY,
    MARKDOWNLINT_DEFAULT_TIMEOUT,
    MARKDOWNLINT_FILE_PATTERNS,
    MarkdownlintPlugin,
)

__all__ = [
    "MARKDOWNLINT_DEFAULT_PRIORITY",
    "MARKDOWNLINT_DEFAULT_TIMEOUT",
    "MARKDOWNLINT_FILE_PATTERNS",
    "MarkdownlintPlugin",
]
