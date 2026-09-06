"""Re-export shim for the import-linter tool definition (#2311).

The import-linter plugin now lives in its own package,
:mod:`lintro.tools.import_linter`. Plugin discovery still scans this package, so
importing this module registers the tool. Deleted once discovery moves
to the per-tool packages.
"""

from lintro.tools.import_linter.definition import (
    IMPORT_LINTER_CONFIG_FILES,
    IMPORT_LINTER_DEFAULT_PRIORITY,
    IMPORT_LINTER_DEFAULT_TIMEOUT,
    IMPORT_LINTER_FILE_PATTERNS,
    ImportLinterPlugin,
    find_import_linter_config,
)

__all__ = [
    "IMPORT_LINTER_CONFIG_FILES",
    "IMPORT_LINTER_DEFAULT_PRIORITY",
    "IMPORT_LINTER_DEFAULT_TIMEOUT",
    "IMPORT_LINTER_FILE_PATTERNS",
    "ImportLinterPlugin",
    "find_import_linter_config",
]
