"""Html-validate tool package.

Everything the ``html_validate`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.html_validate.definition`. ``lintro.tools.definitions.html_validate``
re-exports the plugin so plugin discovery keeps finding it (#2311).
"""

from lintro.tools.html_validate.definition import (
    HTML_VALIDATE_CONFIG_FILENAMES,
    HTML_VALIDATE_DEFAULT_PRIORITY,
    HTML_VALIDATE_DEFAULT_TIMEOUT,
    HTML_VALIDATE_FILE_PATTERNS,
    HtmlValidatePlugin,
    contains_glob_syntax,
)

__all__ = [
    "HTML_VALIDATE_CONFIG_FILENAMES",
    "HTML_VALIDATE_DEFAULT_PRIORITY",
    "HTML_VALIDATE_DEFAULT_TIMEOUT",
    "HTML_VALIDATE_FILE_PATTERNS",
    "HtmlValidatePlugin",
    "contains_glob_syntax",
]
