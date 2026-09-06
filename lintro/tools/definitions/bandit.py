"""Re-export shim for the bandit tool definition (#2311).

The bandit plugin now lives in its own package, :mod:`lintro.tools.bandit`.
Plugin discovery still scans this package, so importing this module registers
the tool. Deleted once discovery moves to the per-tool packages.
"""

from lintro.tools.bandit.definition import (
    BANDIT_DEFAULT_PRIORITY,
    BANDIT_DEFAULT_TIMEOUT,
    BANDIT_FILE_PATTERNS,
    BANDIT_OUTPUT_FORMAT,
    BanditPlugin,
)

__all__ = [
    "BANDIT_DEFAULT_PRIORITY",
    "BANDIT_DEFAULT_TIMEOUT",
    "BANDIT_FILE_PATTERNS",
    "BANDIT_OUTPUT_FORMAT",
    "BanditPlugin",
]
