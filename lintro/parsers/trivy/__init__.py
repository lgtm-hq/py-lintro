"""Trivy parser module."""

from lintro.parsers.trivy.trivy_issue import TrivyIssue
from lintro.parsers.trivy.trivy_parser import parse_trivy_output

__all__ = ["TrivyIssue", "parse_trivy_output"]
