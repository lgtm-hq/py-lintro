"""Buf parser module.

This module provides parsing functionality for buf Protocol Buffer
linter/formatter output.
"""

from __future__ import annotations

from lintro.parsers.buf.buf_issue import BufIssue
from lintro.parsers.buf.buf_parser import (
    parse_buf_format_output,
    parse_buf_output,
)

__all__ = ["BufIssue", "parse_buf_format_output", "parse_buf_output"]
