"""Re-export shim for the commitlint tool definition (#2311).

The commitlint plugin now lives in its own package, :mod:`lintro.tools.commitlint`.
Plugin discovery still scans this package, so importing this module registers
the tool. Deleted once discovery moves to the per-tool packages.
"""

from lintro.tools.commitlint.definition import (
    COMMITLINT_CONFIG_MISSING_EXIT,
    COMMITLINT_DEFAULT_PRIORITY,
    COMMITLINT_DEFAULT_TIMEOUT,
    COMMITLINT_FILE_PATTERNS,
    CommitlintPlugin,
)

__all__ = [
    "COMMITLINT_CONFIG_MISSING_EXIT",
    "COMMITLINT_DEFAULT_PRIORITY",
    "COMMITLINT_DEFAULT_TIMEOUT",
    "COMMITLINT_FILE_PATTERNS",
    "CommitlintPlugin",
]
