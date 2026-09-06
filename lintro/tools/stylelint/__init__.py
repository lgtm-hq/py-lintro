"""Stylelint tool package.

Everything the ``stylelint`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.stylelint.definition`. Plugin discovery enters the package
through that module (#2311).
"""

from lintro.tools.stylelint.definition import (
    STYLELINT_CONFIG_FILENAMES,
    STYLELINT_DEFAULT_PRIORITY,
    STYLELINT_DEFAULT_TIMEOUT,
    STYLELINT_FILE_PATTERNS,
    STYLELINT_PSEUDO_RULES,
    StylelintPlugin,
)

__all__ = [
    "STYLELINT_CONFIG_FILENAMES",
    "STYLELINT_DEFAULT_PRIORITY",
    "STYLELINT_DEFAULT_TIMEOUT",
    "STYLELINT_FILE_PATTERNS",
    "STYLELINT_PSEUDO_RULES",
    "StylelintPlugin",
]
