"""Re-export shim for the html_validate tool definition (#2311).

The html_validate plugin now lives in its own package,
:mod:`lintro.tools.html_validate`. Plugin discovery still scans this package, so
importing this module registers the tool. Deleted once discovery moves
to the per-tool packages.
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
