"""Integration tests for PipAuditPlugin check command.

pip-audit queries the PyPI Advisory Database / OSV over the network, so these
tests require both the ``pip-audit`` binary and network access. They are
skipped when the binary is absent.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

# Skip all tests if pip-audit is not installed
pytestmark = pytest.mark.skipif(
    shutil.which("pip-audit") is None,
    reason="pip-audit not installed",
)


def test_check_file_with_vulnerabilities(
    get_plugin: Callable[[str], BaseToolPlugin],
    pip_audit_violation_file: str,
) -> None:
    """Verify pip-audit detects vulnerabilities in known-vulnerable packages.

    Uses the pip_audit_violations.txt fixture which pins packages with
    historically known vulnerabilities (jinja2==2.11.2, requests==2.19.0).

    Args:
        get_plugin: Fixture factory to get plugin instances.
        pip_audit_violation_file: Path to vulnerable lockfile from test_samples.
    """
    plugin = get_plugin("pip_audit")
    result = plugin.check([pip_audit_violation_file], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("pip_audit")
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)


def test_check_clean_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    pip_audit_clean_file: str,
) -> None:
    """Verify pip-audit passes on a lockfile with no known vulnerabilities.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        pip_audit_clean_file: Path to clean lockfile from test_samples.
    """
    plugin = get_plugin("pip_audit")
    result = plugin.check([pip_audit_clean_file], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("pip_audit")
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_empty_directory(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify pip-audit check handles empty directories gracefully.

    With no requirements or project files present, pip-audit has nothing to
    audit and reports a clean, successful result with no vulnerabilities.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    plugin = get_plugin("pip_audit")
    result = plugin.check([str(tmp_path)], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("pip_audit")
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
