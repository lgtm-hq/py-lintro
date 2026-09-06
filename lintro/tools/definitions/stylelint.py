"""Re-export shim for the stylelint tool definition (#2311).

The stylelint plugin now lives in its own package,
:mod:`lintro.tools.stylelint`. Plugin discovery still scans this package, so
importing this module registers the tool. Deleted once discovery moves
to the per-tool packages.
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
