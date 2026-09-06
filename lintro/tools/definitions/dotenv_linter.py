"""Re-export shim for the dotenv-linter tool definition (#2311).

The dotenv-linter plugin now lives in its own package,
:mod:`lintro.tools.dotenv_linter`. Plugin discovery still scans this package, so
importing this module registers the tool. Deleted once discovery moves
to the per-tool packages.
"""

from lintro.tools.dotenv_linter.definition import (
    DOTENV_LINTER_DEFAULT_PRIORITY,
    DOTENV_LINTER_DEFAULT_TIMEOUT,
    DOTENV_LINTER_FILE_PATTERNS,
    DotenvLinterPlugin,
)

__all__ = [
    "DOTENV_LINTER_DEFAULT_PRIORITY",
    "DOTENV_LINTER_DEFAULT_TIMEOUT",
    "DOTENV_LINTER_FILE_PATTERNS",
    "DotenvLinterPlugin",
]
