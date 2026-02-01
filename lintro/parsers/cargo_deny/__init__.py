"""cargo-deny parser module."""

from lintro.parsers.cargo_deny.cargo_deny_issue import CargoDenyIssue
from lintro.parsers.cargo_deny.cargo_deny_parser import parse_cargo_deny_output

__all__ = ["CargoDenyIssue", "parse_cargo_deny_output"]
