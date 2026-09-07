"""Cppcheck parser module."""

from lintro.parsers.cppcheck.cppcheck_issue import CppcheckIssue
from lintro.parsers.cppcheck.cppcheck_parser import parse_cppcheck_output

__all__ = ["CppcheckIssue", "parse_cppcheck_output"]
