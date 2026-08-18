"""Pin the MCP extra and lockfile to SDK 2.x, and keep 1.x imports out."""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_UV_LOCK = _REPO_ROOT / "uv.lock"
_MCP_FLOOR = "mcp>=2,<3"
_SOURCE_ROOTS = (_REPO_ROOT / "lintro", _REPO_ROOT / "tests")


def _mcp_shared_memory_imports(source: str) -> list[str]:
    """Return ``mcp.shared.memory`` import forms found in ``source``.

    Args:
        source: Python source to scan.

    Returns:
        Human-readable import forms, empty when none are present.
    """
    tree = ast.parse(source)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "mcp.shared.memory" or alias.name.startswith(
                    "mcp.shared.memory.",
                ):
                    found.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "mcp.shared.memory" or module.startswith("mcp.shared.memory."):
                names = ", ".join(alias.name for alias in node.names)
                found.append(f"from {module} import {names}")
            elif module == "mcp.shared" and any(
                alias.name == "memory" for alias in node.names
            ):
                found.append("from mcp.shared import memory")
    return found


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


def test_forbidden_import_detector_covers_aliased_and_from_forms() -> None:
    """``from mcp.shared import memory`` is a 1.x import, not only the dotted form."""
    assert_that(
        _mcp_shared_memory_imports("from mcp.shared.memory import create_session"),
    ).is_not_empty()
    assert_that(
        _mcp_shared_memory_imports("import mcp.shared.memory as memory"),
    ).is_not_empty()
    assert_that(
        _mcp_shared_memory_imports("from mcp.shared import memory"),
    ).is_not_empty()
    assert_that(
        _mcp_shared_memory_imports("from mcp.shared import memory as memory"),
    ).is_not_empty()
    assert_that(_mcp_shared_memory_imports("from mcp.client import Client")).is_empty()


def test_no_sdk_1x_memory_helper_imports_remain() -> None:
    """``mcp.shared.memory`` was removed in 2.0; tests must not import it."""
    offenders: list[str] = []
    for root in _SOURCE_ROOTS:
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            try:
                hits = _mcp_shared_memory_imports(text)
            except SyntaxError:
                continue
            if hits:
                offenders.append(str(path.relative_to(_REPO_ROOT)))
    assert_that(offenders).is_empty()


def test_sdk_types_serialize_camelcase_jsonrpc_aliases() -> None:
    """Python snake_case fields must still dump as MCP wire camelCase."""
    from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations

    tool = Tool(
        name="lintro_ping",
        description="health",
        input_schema={"type": "object"},
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
        ),
    )
    tool_dump = tool.model_dump(by_alias=True)
    assert_that(tool_dump).contains_key("inputSchema")
    assert_that(tool_dump["annotations"]["readOnlyHint"]).is_true()
    assert_that(tool_dump["annotations"]["destructiveHint"]).is_false()
    assert_that(tool_dump["annotations"]["idempotentHint"]).is_true()

    result = CallToolResult(
        is_error=True,
        structured_content={"error": {"code": "invalid_input"}},
        content=[TextContent(type="text", text="{}")],
    )
    result_dump = result.model_dump(by_alias=True)
    assert_that(result_dump["isError"]).is_true()
    assert_that(result_dump).contains_key("structuredContent")
