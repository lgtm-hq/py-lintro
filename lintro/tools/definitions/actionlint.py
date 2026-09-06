"""Re-export shim for the actionlint tool definition (#2311).

The actionlint plugin now lives in its own package, :mod:`lintro.tools.actionlint`.
Plugin discovery still scans this package, so importing this module registers
the tool. Deleted once discovery moves to the per-tool packages.
"""

from lintro.tools.actionlint.definition import (
    ACTIONLINT_DEFAULT_PRIORITY,
    ACTIONLINT_DEFAULT_TIMEOUT,
    ACTIONLINT_FILE_PATTERNS,
    ActionlintPlugin,
)

__all__ = [
    "ACTIONLINT_DEFAULT_PRIORITY",
    "ACTIONLINT_DEFAULT_TIMEOUT",
    "ACTIONLINT_FILE_PATTERNS",
    "ActionlintPlugin",
]
