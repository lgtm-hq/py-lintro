"""Output utilities for Lintro.

This package provides output-related functionality:
- OutputManager for timestamped run directories
- write_output_file for user-specified output files
- format_tool_output for tool output formatting
"""

from lintro.utils.output.constants import (
    DEFAULT_BASE_DIR,
    DEFAULT_KEEP_LAST,
    DEFAULT_RUN_PREFIX,
    DEFAULT_TEMP_PREFIX,
    DEFAULT_TIMESTAMP_FORMAT,
)
from lintro.utils.output.file_writer import (
    JsonReportExtras,
    format_tool_output,
    write_output_file,
)
from lintro.utils.output.helpers import html_escape, markdown_escape, sanitize_csv_value
from lintro.utils.output.manager import OutputManager

__all__ = [
    # Constants
    "DEFAULT_BASE_DIR",
    "DEFAULT_KEEP_LAST",
    "DEFAULT_TIMESTAMP_FORMAT",
    "DEFAULT_RUN_PREFIX",
    "DEFAULT_TEMP_PREFIX",
    # Classes
    "OutputManager",
    # Functions
    "JsonReportExtras",
    "write_output_file",
    "format_tool_output",
    # Helpers
    "markdown_escape",
    "html_escape",
    "sanitize_csv_value",
]
