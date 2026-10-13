"""Integration tests for ``lintro check`` with the terraform tool."""

from __future__ import annotations

from typing import cast

import pytest
from assertpy import assert_that

from lintro.parsers.terraform.terraform_issue import TerraformIssue
from lintro.plugins import ToolRegistry


@pytest.mark.terraform
def test_terraform_reports_fmt_violations(terraform_violation_module: str) -> None:
    """Lintro reports formatting issues for an unformatted module.

    Args:
        terraform_violation_module: Module directory with an unformatted file.
    """
    tool = ToolRegistry.get("terraform")
    assert_that(tool).is_not_none()
    tool.set_options(validate=False)
    result = tool.check([terraform_violation_module], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)
    issues = cast("list[TerraformIssue]", result.issues or [])
    assert_that([i.code for i in issues]).contains("fmt")


@pytest.mark.terraform
def test_terraform_reports_validate_errors(terraform_broken_module: str) -> None:
    """Lintro reports validation diagnostics for a broken module.

    Args:
        terraform_broken_module: Module directory that fails ``terraform validate``.
    """
    tool = ToolRegistry.get("terraform")
    assert_that(tool).is_not_none()
    tool.set_options(validate=True)
    result = tool.check([terraform_broken_module], {})

    assert_that(result.success).is_false()
    issues = cast("list[TerraformIssue]", result.issues or [])
    assert_that([i.code for i in issues]).contains("validate")


@pytest.mark.terraform
def test_terraform_clean_module_passes(terraform_clean_module: str) -> None:
    """Lintro reports no issues for a clean, formatted, valid module.

    Args:
        terraform_clean_module: Module directory with a clean, valid file.
    """
    tool = ToolRegistry.get("terraform")
    assert_that(tool).is_not_none()
    tool.set_options(validate=True)
    result = tool.check([terraform_clean_module], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
