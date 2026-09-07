"""Markdownlint tool package.

Everything the ``markdownlint`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.markdownlint.definition`. Plugin discovery enters the package
through that module (#2311).
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
