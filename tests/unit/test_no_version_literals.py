"""Meta-guard: tool tests must not hardcode their tool's canonical version.

A test that asserts a tool's ``min_version`` or ``version`` field equals a
specific literal is a value-tautology when that literal is also defined as a
constant in ``_tool_versions.py``. The assertion adds no signal — the
generator's own tests already verify the constant matches its source — and
guarantees CI failure on every dependency bump.

This guard greps every Python test file under
``tests/unit/tools/<tool>/`` for its tool's pinned version string. Any hit is
a violation. To suppress on a legitimate fixture line (vanishingly rare) add
``# meta-guard: allow`` to the same line.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro._tool_versions import (
    _NPM_VERSIONS_BY_TOOL,
    _PYPI_VERSIONS_BY_TOOL,
    TOOL_VERSIONS,
)
from lintro.enums.tool_name import ToolName

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_TEST_DIR = REPO_ROOT / "tests" / "unit" / "tools"
ALLOW_MARKER = "meta-guard: allow"


def _canonical_versions() -> dict[ToolName, str]:
    """Collect canonical tool versions from every source.

    Returns:
        Mapping of ToolName -> canonical version string.
    """
    return {
        tool: version
        for source in (TOOL_VERSIONS, _NPM_VERSIONS_BY_TOOL, _PYPI_VERSIONS_BY_TOOL)
        for tool, version in source.items()
        if isinstance(tool, ToolName)
    }


@pytest.mark.parametrize(
    "tool",
    list(_canonical_versions()),
    ids=lambda t: t.value,
)
def test_tool_test_dir_has_no_version_literal(tool: ToolName) -> None:
    """Each tool's test directory has no occurrence of its pinned version.

    Args:
        tool: The tool to scan tests for.
    """
    version = _canonical_versions()[tool]
    tool_dir = TOOLS_TEST_DIR / tool.value
    if not tool_dir.exists():
        pytest.skip(f"no test dir for {tool.value}")

    violations: list[str] = []
    for py_file in tool_dir.rglob("*.py"):
        for lineno, line in enumerate(py_file.read_text().splitlines(), start=1):
            if ALLOW_MARKER in line:
                continue
            if version in line:
                violations.append(
                    f"{py_file.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}",
                )

    assert_that(
        violations,
        description=(
            f"{tool.value} canonical version {version!r} must not be hardcoded in "
            f"its test directory; source from lintro._tool_versions or delete "
            f"the assertion as a value-tautology."
        ),
    ).is_empty()
