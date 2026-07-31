"""Parser for html-validate output."""

from lintro.parsers.html_validate.html_validate_issue import HtmlValidateIssue
from lintro.parsers.html_validate.html_validate_parser import (
    parse_html_validate_output,
)

__all__ = ["HtmlValidateIssue", "parse_html_validate_output"]
