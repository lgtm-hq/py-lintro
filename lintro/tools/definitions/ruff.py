"""Re-export shim for the ruff tool definition (#2311).

The ruff plugin now lives in its own package, :mod:`lintro.tools.ruff`, next
to the command builders and executors it uses. Plugin discovery still scans
this package, so importing this module registers the tool. Deleted once
discovery moves to the per-tool packages.
"""

from lintro.tools.ruff.definition import (
    RUFF_DEFAULT_PRIORITY,
    RUFF_DEFAULT_TIMEOUT,
    RUFF_FILE_PATTERNS,
    RUFF_OUTPUT_FORMAT,
    RUFF_TEST_MODE_ENV,
    RUFF_TEST_MODE_VALUE,
    RuffPlugin,
)

__all__ = [
    "RUFF_DEFAULT_PRIORITY",
    "RUFF_DEFAULT_TIMEOUT",
    "RUFF_FILE_PATTERNS",
    "RUFF_OUTPUT_FORMAT",
    "RUFF_TEST_MODE_ENV",
    "RUFF_TEST_MODE_VALUE",
    "RuffPlugin",
]
