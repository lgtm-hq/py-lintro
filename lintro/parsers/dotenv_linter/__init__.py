"""dotenv-linter parser module.

Provides parsing functionality for dotenv-linter output. dotenv-linter is a
fast, Rust-based linter for ``.env`` files that detects issues such as
duplicate keys, lowercase keys, and incorrect formatting.
"""

from lintro.parsers.dotenv_linter.dotenv_linter_issue import DotenvLinterIssue
from lintro.parsers.dotenv_linter.dotenv_linter_parser import (
    parse_dotenv_linter_output,
)

__all__ = ["DotenvLinterIssue", "parse_dotenv_linter_output"]
