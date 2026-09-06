"""Pylint tool package.

Everything the ``pylint`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.pylint.definition`. Plugin discovery enters the package
through that module (#2311).
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
