"""Re-export shim for the pylint tool definition (#2311).

The pylint plugin now lives in its own package,
:mod:`lintro.tools.pylint`. Plugin discovery still scans this package, so
importing this module registers the tool. Deleted once discovery moves
to the per-tool packages.
"""

from lintro.tools.pylint.definition import (
    PYLINT_ANALYSED_METADATA_KEY,
    PYLINT_CONFIG_FILES,
    PYLINT_DEFAULT_PRIORITY,
    PYLINT_DEFAULT_TIMEOUT,
    PYLINT_FILE_PATTERNS,
    PYLINT_NO_INCLUDED_FILES,
    PYLINT_NOTHING_TO_LINT,
    PYLINT_OUTPUT_FORMAT,
    PylintPlugin,
    filter_included_files,
    find_pylint_config,
)

__all__ = [
    "PYLINT_ANALYSED_METADATA_KEY",
    "PYLINT_CONFIG_FILES",
    "PYLINT_DEFAULT_PRIORITY",
    "PYLINT_DEFAULT_TIMEOUT",
    "PYLINT_FILE_PATTERNS",
    "PYLINT_NOTHING_TO_LINT",
    "PYLINT_NO_INCLUDED_FILES",
    "PYLINT_OUTPUT_FORMAT",
    "PylintPlugin",
    "filter_included_files",
    "find_pylint_config",
]
