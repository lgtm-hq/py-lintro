"""Every security scanner carries the security classification (#3568).

The version gate fails the run for a security scanner it refuses, or whose
version it cannot read, and decides that from ``ToolType.SECURITY`` on the
tool definition. A scanner that lost the flag would quietly be skipped
instead, so the scanners are listed here by name.
"""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.enums.tool_type import ToolType
from lintro.tools import tool_manager

#: The security scanners lintro wraps, by registry name.
SECURITY_SCANNERS: tuple[str, ...] = (
    "bandit",
    "cargo_audit",
    "cargo_deny",
    "checkov",
    "cppcheck",
    "gitleaks",
    "osv_scanner",
    "pip_audit",
    "semgrep",
    "trufflehog",
)


@pytest.mark.parametrize("name", SECURITY_SCANNERS)
def test_security_scanner_is_classified_as_security(name: str) -> None:
    """The named scanner's definition includes ``ToolType.SECURITY``.

    Args:
        name: Registry name of the scanner.
    """
    definition = tool_manager.get_tool(name).definition

    assert_that(definition.name).is_equal_to(name)
    assert_that(bool(definition.tool_type & ToolType.SECURITY)).is_true()


def test_no_other_tool_is_classified_as_security() -> None:
    """A new scanner is added to the list above when it gains the flag."""
    classified = sorted(
        name
        for name, plugin in tool_manager.get_all_tools().items()
        if plugin.definition.tool_type & ToolType.SECURITY
    )

    assert_that(classified).is_equal_to(sorted(SECURITY_SCANNERS))
