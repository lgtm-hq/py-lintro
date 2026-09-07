"""Re-export shim for the sqlfluff tool definition (#2311).

The sqlfluff plugin now lives in its own package,
:mod:`lintro.tools.sqlfluff`. Plugin discovery still scans this package, so
importing this module registers the tool. Deleted once discovery moves
to the per-tool packages.
"""

from lintro.tools.sqlfluff.definition import (
    SQLFLUFF_DEFAULT_FORMAT,
    SQLFLUFF_DEFAULT_PRIORITY,
    SQLFLUFF_DEFAULT_TIMEOUT,
    SQLFLUFF_FILE_PATTERNS,
    SqlfluffPlugin,
)

__all__ = [
    "SQLFLUFF_DEFAULT_FORMAT",
    "SQLFLUFF_DEFAULT_PRIORITY",
    "SQLFLUFF_DEFAULT_TIMEOUT",
    "SQLFLUFF_FILE_PATTERNS",
    "SqlfluffPlugin",
]
