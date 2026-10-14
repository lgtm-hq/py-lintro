"""Integration tests for ``lintro format`` with the terraform tool."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.plugins import ToolRegistry


@pytest.mark.terraform
def test_terraform_fix_formats_module(terraform_violation_module: str) -> None:
    """Lintro's fix reformats an unformatted module in place.

    Args:
        terraform_violation_module: Module directory with an unformatted file.
    """
    tool = ToolRegistry.get("terraform")
    assert_that(tool).is_not_none()
    tool.set_options(validate=False)
    result = tool.fix([terraform_violation_module], {})

    assert_that(result.success).is_true()
    assert_that(result.fixed_issues_count).is_greater_than(0)

    # A subsequent check must be clean after formatting.
    tool.set_options(validate=False)
    recheck = tool.check([terraform_violation_module], {})
    assert_that(recheck.issues_count).is_equal_to(0)
