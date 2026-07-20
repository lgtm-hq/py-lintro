"""Integration tests for the terraform tool.

These tests exercise the real ``terraform`` binary against committed fixtures.
They are skipped when terraform is not installed so local runs without the
binary behave like a clean skip rather than a failure.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - subprocess drives the real terraform binary; shell=False
from pathlib import Path
from typing import cast

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.parsers.terraform.terraform_issue import TerraformIssue
from lintro.plugins import ToolRegistry

logger.remove()
logger.add(lambda msg: print(msg, end=""), level="INFO")

_SAMPLES = Path("test_samples/tools/config/terraform")


def terraform_available() -> bool:
    """Return True if the ``terraform`` binary is available on PATH.

    Returns:
        bool: True when ``terraform version`` succeeds, False otherwise.
    """
    if shutil.which("terraform") is None:
        return False
    try:
        proc = subprocess.run(  # nosec B603 B607 - fixed argv, real binary from PATH, shell=False
            ["terraform", "version"],
            capture_output=True,
            text=True,
        )
        return proc.returncode == 0
    except FileNotFoundError:
        return False


@pytest.mark.terraform
def test_terraform_available() -> None:
    """Skip the suite when terraform is not present locally."""
    if not terraform_available():
        pytest.skip("terraform not available")


@pytest.mark.terraform
def test_terraform_reports_fmt_violations(tmp_path: Path) -> None:
    """Lintro reports formatting issues for an unformatted module.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    if not terraform_available():
        pytest.skip("terraform not available")

    module = tmp_path / "fmt"
    module.mkdir()
    (module / "main.tf").write_text(
        (_SAMPLES / "terraform_violations.tf").read_text(),
    )

    tool = ToolRegistry.get("terraform")
    assert_that(tool).is_not_none()
    tool.set_options(validate=False)
    result = tool.check([str(module)], {})

    logger.info(f"[LOG] lintro terraform fmt issues: {result.issues_count}")
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)
    issues = cast("list[TerraformIssue]", result.issues or [])
    assert_that([i.code for i in issues]).contains("fmt")


@pytest.mark.terraform
def test_terraform_reports_validate_errors(tmp_path: Path) -> None:
    """Lintro reports validation diagnostics for a broken module.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    if not terraform_available():
        pytest.skip("terraform not available")

    module = tmp_path / "broken"
    module.mkdir()
    (module / "main.tf").write_text(
        (_SAMPLES / "validate_broken" / "main.tf").read_text(),
    )

    tool = ToolRegistry.get("terraform")
    assert_that(tool).is_not_none()
    tool.set_options(validate=True)
    result = tool.check([str(module)], {})

    logger.info(f"[LOG] lintro terraform validate issues: {result.issues_count}")
    assert_that(result.success).is_false()
    issues = cast("list[TerraformIssue]", result.issues or [])
    validate_codes = [i.code for i in issues]
    assert_that(validate_codes).contains("validate")


@pytest.mark.terraform
def test_terraform_clean_module_passes(tmp_path: Path) -> None:
    """Lintro reports no issues for a clean, formatted, valid module.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    if not terraform_available():
        pytest.skip("terraform not available")

    module = tmp_path / "clean"
    module.mkdir()
    (module / "main.tf").write_text(
        (_SAMPLES / "clean" / "main.tf").read_text(),
    )

    tool = ToolRegistry.get("terraform")
    assert_that(tool).is_not_none()
    tool.set_options(validate=True)
    result = tool.check([str(module)], {})

    logger.info(f"[LOG] lintro terraform clean issues: {result.issues_count}")
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


@pytest.mark.terraform
def test_terraform_fix_formats_module(tmp_path: Path) -> None:
    """Lintro's fix reformats an unformatted module in place.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    if not terraform_available():
        pytest.skip("terraform not available")

    module = tmp_path / "fix"
    module.mkdir()
    target = module / "main.tf"
    target.write_text((_SAMPLES / "terraform_violations.tf").read_text())

    tool = ToolRegistry.get("terraform")
    assert_that(tool).is_not_none()
    tool.set_options(validate=False)
    result = tool.fix([str(module)], {})

    logger.info(f"[LOG] lintro terraform fixed: {result.fixed_issues_count}")
    assert_that(result.success).is_true()
    assert_that(result.fixed_issues_count).is_greater_than(0)

    # A subsequent check must be clean after formatting.
    tool.set_options(validate=False)
    recheck = tool.check([str(module)], {})
    assert_that(recheck.issues_count).is_equal_to(0)
