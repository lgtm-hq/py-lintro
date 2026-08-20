"""Terraform parser module.

This module provides parsing functionality for ``terraform fmt`` and
``terraform validate`` output.
"""

from __future__ import annotations

from lintro.parsers.terraform.terraform_issue import TerraformIssue
from lintro.parsers.terraform.terraform_parser import (
    parse_terraform_fmt_output,
    parse_terraform_output,
    parse_terraform_validate_output,
)

__all__ = [
    "TerraformIssue",
    "parse_terraform_fmt_output",
    "parse_terraform_output",
    "parse_terraform_validate_output",
]
