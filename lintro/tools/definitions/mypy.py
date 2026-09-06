"""Re-export shim for the mypy tool definition (#2311).

The mypy plugin now lives in its own package,
:mod:`lintro.tools.mypy`. Plugin discovery still scans this package, so
importing this module registers the tool. Deleted once discovery moves
to the per-tool packages.
"""

from lintro.tools.mypy.definition import (
    MYPY_DEFAULT_EXCLUDE_PATTERNS,
    MYPY_DEFAULT_PRIORITY,
    MYPY_DEFAULT_TIMEOUT,
    MYPY_FILE_PATTERNS,
    MYPY_NO_FILES_MARKER,
    MYPY_OPTION_TYPES,
    MypyPlugin,
)

__all__ = [
    "MYPY_DEFAULT_EXCLUDE_PATTERNS",
    "MYPY_DEFAULT_PRIORITY",
    "MYPY_DEFAULT_TIMEOUT",
    "MYPY_FILE_PATTERNS",
    "MYPY_NO_FILES_MARKER",
    "MYPY_OPTION_TYPES",
    "MypyPlugin",
]
