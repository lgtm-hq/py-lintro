"""Re-export shim for the semgrep tool definition (#2311).

The semgrep plugin now lives in its own package,
:mod:`lintro.tools.semgrep`. Plugin discovery still scans this package, so
importing this module registers the tool. Deleted once discovery moves
to the per-tool packages.
"""

from lintro.tools.semgrep.definition import (
    SEMGREP_DEFAULT_CONFIG,
    SEMGREP_DEFAULT_PRIORITY,
    SEMGREP_DEFAULT_TIMEOUT,
    SEMGREP_FILE_PATTERNS,
    SEMGREP_OUTPUT_FORMAT,
    SemgrepPlugin,
)

__all__ = [
    "SEMGREP_DEFAULT_CONFIG",
    "SEMGREP_DEFAULT_PRIORITY",
    "SEMGREP_DEFAULT_TIMEOUT",
    "SEMGREP_FILE_PATTERNS",
    "SEMGREP_OUTPUT_FORMAT",
    "SemgrepPlugin",
]
