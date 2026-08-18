"""Pin the MCP extra and lockfile to SDK 2.x, and keep 1.x imports out."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_UV_LOCK = _REPO_ROOT / "uv.lock"
_MCP_FLOOR = "mcp>=2,<3"
_SOURCE_ROOTS = (_REPO_ROOT / "lintro", _REPO_ROOT / "tests")
_FORBIDDEN_IMPORT = re.compile(
    r"^\s*(?:from\s+mcp\.shared\.memory\s+import|import\s+mcp\.shared\.memory)\b",
    re.MULTILINE,
)


def test_mcp_extra_and_dev_group_require_sdk_2() -> None:
    """The extra and the default dev group share the 2.x floor and 3.x ceiling."""
    pyproject = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    extras = pyproject["project"]["optional-dependencies"]
    assert_that(extras["mcp"]).is_equal_to([_MCP_FLOOR])
    assert_that(pyproject["dependency-groups"]["dev"]).contains(_MCP_FLOOR)


def test_uv_lock_resolves_a_single_mcp_2_x() -> None:
    """``uv.lock`` must resolve ``mcp`` 2.x, not a 1.x leftover."""
    text = _UV_LOCK.read_text(encoding="utf-8")
    match = re.search(
        r'(?m)^name = "mcp"\nversion = "([^"]+)"',
        text,
    )
    assert_that(match).is_not_none()
    assert match is not None
    version = match.group(1)
    assert_that(version).starts_with("2.")
    assert_that(re.findall(r'(?m)^name = "mcp"$', text)).is_length(1)


def test_no_sdk_1x_memory_helper_imports_remain() -> None:
    """``mcp.shared.memory`` was removed in 2.0; tests must not import it."""
    offenders: list[str] = []
    for root in _SOURCE_ROOTS:
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if _FORBIDDEN_IMPORT.search(text):
                offenders.append(str(path.relative_to(_REPO_ROOT)))
    assert_that(offenders).is_empty()
