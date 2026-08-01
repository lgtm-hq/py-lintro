"""End-to-end MCP client/server session tests over in-memory streams.

These exercise the same ``Server`` object the stdio transport serves, through a
real :class:`mcp.ClientSession` (initialize handshake, ``tools/list``,
``tools/call``), without spawning a subprocess. The subprocess variant lives in
``tests/integration/test_mcp_server.py``; CI runs the test suite with
``--ignore=tests/integration``, so this file is what keeps the protocol surface
covered on every PR.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

from assertpy import assert_that
from mcp import ClientSession, types
from mcp.shared.memory import create_connected_server_and_client_session

from lintro import __version__
from lintro.mcp.errors import McpError, McpErrorCode
from lintro.mcp.registry import McpToolRegistry, McpToolSpec
from lintro.mcp.server import build_default_registry, create_mcp_server

_T = TypeVar("_T")


def _run_session(
    *,
    workspace: Path,
    registry: McpToolRegistry | None,
    check: Callable[[ClientSession], Awaitable[_T]],
) -> _T:
    """Run ``check`` against a connected in-memory MCP client session.

    Args:
        workspace: Workspace root for the server under test.
        registry: Optional registry override; defaults to the built-ins.
        check: Async callback receiving an initialized client session.

    Returns:
        Whatever ``check`` returns.
    """
    server = create_mcp_server(workspace=workspace, registry=registry)

    async def _main() -> _T:
        async with create_connected_server_and_client_session(server) as session:
            return await check(session)

    return asyncio.run(_main())


def _payload(result: types.CallToolResult) -> dict[str, Any]:
    """Extract a tool result payload as a dict.

    Prefers ``structuredContent`` and falls back to parsing the JSON text
    block, so the assertions hold regardless of which the SDK populates.

    Args:
        result: The ``CallToolResult`` returned by ``session.call_tool``.

    Returns:
        The payload the server sent.
    """
    if result.structuredContent:
        return dict(result.structuredContent)
    block = result.content[0]
    assert isinstance(block, types.TextContent)
    return dict(json.loads(block.text))


def _failing_registry(workspace: Path) -> McpToolRegistry:
    """Build a registry with tools that exercise each failure mode.

    Args:
        workspace: Workspace root used for the built-in tools.

    Returns:
        A registry with ``lintro_ping`` plus strict/boom/paths test tools.
    """
    registry = build_default_registry(workspace=workspace)

    def _boom(_arguments: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("handler exploded")

    registry.register(
        spec=McpToolSpec(
            name="demo_boom",
            description="always raises",
            input_schema={"type": "object", "properties": {}},
            handler=_boom,
        ),
    )
    registry.register(
        spec=McpToolSpec(
            name="demo_strict",
            description="requires a name",
            input_schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
                "additionalProperties": False,
            },
            handler=lambda arguments: {"name": arguments["name"]},
        ),
    )
    registry.register(
        spec=McpToolSpec(
            name="demo_paths",
            description="echoes a resolved path",
            input_schema={
                "type": "object",
                "properties": {"target": {"type": "string"}},
                "required": ["target"],
                "additionalProperties": False,
            },
            handler=lambda arguments: {"target": arguments["target"]},
            path_arguments=("target",),
        ),
    )

    async def _async_echo(arguments: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0)
        return {"echo": arguments.get("value", "")}

    registry.register(
        spec=McpToolSpec(
            name="demo_async",
            description="async handler",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "additionalProperties": False,
            },
            handler=_async_echo,
        ),
    )

    def _sleep_forever(_arguments: dict[str, Any]) -> dict[str, Any]:
        time.sleep(30)
        return {"never": "returned"}

    registry.register(
        spec=McpToolSpec(
            name="demo_slow",
            description="sync handler that outlives its budget",
            input_schema={"type": "object", "properties": {}},
            handler=_sleep_forever,
            timeout_seconds=0.05,
        ),
    )

    def _explicit_error(_arguments: dict[str, Any]) -> dict[str, Any]:
        raise McpError(
            code=McpErrorCode.TOOL_UNAVAILABLE,
            message="backing binary missing",
            detail={"binary": "ruff"},
        )

    registry.register(
        spec=McpToolSpec(
            name="demo_unavailable",
            description="raises a structured McpError",
            input_schema={"type": "object", "properties": {}},
            handler=_explicit_error,
        ),
    )
    return registry


def test_session_lists_ping_with_annotation_hints(tmp_path: Path) -> None:
    """tools/list exposes lintro_ping with read-only annotation hints."""

    async def _check(session: ClientSession) -> None:
        listed = await session.list_tools()
        names = [tool.name for tool in listed.tools]
        assert_that(names).contains("lintro_ping")

        ping = next(tool for tool in listed.tools if tool.name == "lintro_ping")
        annotations = ping.annotations
        assert annotations is not None
        assert_that(annotations.readOnlyHint).is_true()
        assert_that(annotations.destructiveHint).is_false()
        assert_that(annotations.idempotentHint).is_true()

    _run_session(workspace=tmp_path, registry=None, check=_check)


def test_session_call_ping_returns_server_info(tmp_path: Path) -> None:
    """Calling lintro_ping returns status, version, and workspace root."""

    async def _check(session: ClientSession) -> None:
        result = await session.call_tool("lintro_ping", {})
        assert_that(result.isError).is_false()

        payload = _payload(result)
        assert_that(payload["status"]).is_equal_to("ok")
        assert_that(payload["lintro_version"]).is_equal_to(__version__)
        assert_that(payload["workspace"]).is_equal_to(str(tmp_path.resolve()))

    _run_session(workspace=tmp_path, registry=None, check=_check)


def test_session_unknown_tool_returns_tool_unavailable(tmp_path: Path) -> None:
    """An unregistered tool name yields the tool_unavailable envelope."""

    async def _check(session: ClientSession) -> None:
        result = await session.call_tool("lintro_nope", {})
        assert_that(result.isError).is_true()
        assert_that(_payload(result)["error"]["code"]).is_equal_to(
            "tool_unavailable",
        )

    _run_session(
        workspace=tmp_path,
        registry=_failing_registry(tmp_path),
        check=_check,
    )


def test_session_invalid_arguments_return_invalid_input(tmp_path: Path) -> None:
    """Schema-invalid arguments yield the invalid_input envelope, not prose."""

    async def _check(session: ClientSession) -> None:
        result = await session.call_tool("demo_strict", {"name": 42})
        assert_that(result.isError).is_true()
        envelope = _payload(result)["error"]
        assert_that(envelope["code"]).is_equal_to("invalid_input")
        assert_that(envelope["detail"]["tool"]).is_equal_to("demo_strict")

    _run_session(
        workspace=tmp_path,
        registry=_failing_registry(tmp_path),
        check=_check,
    )


def test_session_path_argument_escape_returns_workspace_violation(
    tmp_path: Path,
) -> None:
    """A path argument outside the workspace is refused before dispatch."""

    async def _check(session: ClientSession) -> None:
        result = await session.call_tool("demo_paths", {"target": "/etc/passwd"})
        assert_that(result.isError).is_true()
        assert_that(_payload(result)["error"]["code"]).is_equal_to(
            "workspace_violation",
        )

    _run_session(
        workspace=tmp_path,
        registry=_failing_registry(tmp_path),
        check=_check,
    )


def test_session_path_argument_is_resolved_for_handler(tmp_path: Path) -> None:
    """A contained path argument reaches the handler already resolved."""
    (tmp_path / "a.py").write_text("a = 1\n", encoding="utf-8")

    async def _check(session: ClientSession) -> None:
        result = await session.call_tool("demo_paths", {"target": "a.py"})
        assert_that(result.isError).is_false()
        payload = _payload(result)
        assert_that(payload["target"]).is_equal_to(str((tmp_path / "a.py").resolve()))

    _run_session(
        workspace=tmp_path,
        registry=_failing_registry(tmp_path),
        check=_check,
    )


def test_session_handler_exception_returns_execution_error(tmp_path: Path) -> None:
    """An unexpected handler exception is shaped into execution_error."""

    async def _check(session: ClientSession) -> None:
        result = await session.call_tool("demo_boom", {})
        assert_that(result.isError).is_true()
        envelope = _payload(result)["error"]
        assert_that(envelope["code"]).is_equal_to("execution_error")
        assert_that(envelope["message"]).contains("handler exploded")

    _run_session(
        workspace=tmp_path,
        registry=_failing_registry(tmp_path),
        check=_check,
    )


def test_session_structured_mcp_error_is_preserved(tmp_path: Path) -> None:
    """A handler-raised McpError keeps its own code rather than being wrapped."""

    async def _check(session: ClientSession) -> None:
        result = await session.call_tool("demo_unavailable", {})
        assert_that(result.isError).is_true()
        envelope = _payload(result)["error"]
        assert_that(envelope["code"]).is_equal_to("tool_unavailable")
        assert_that(envelope["detail"]["binary"]).is_equal_to("ruff")

    _run_session(
        workspace=tmp_path,
        registry=_failing_registry(tmp_path),
        check=_check,
    )


def test_session_supports_async_handlers(tmp_path: Path) -> None:
    """Coroutine handlers are awaited before their result is returned."""

    async def _check(session: ClientSession) -> None:
        result = await session.call_tool("demo_async", {"value": "hi"})
        assert_that(result.isError).is_false()
        payload = _payload(result)
        assert_that(payload["echo"]).is_equal_to("hi")

    _run_session(
        workspace=tmp_path,
        registry=_failing_registry(tmp_path),
        check=_check,
    )


def test_session_slow_handler_times_out(tmp_path: Path) -> None:
    """A handler that outlives its budget returns a timeout execution_error."""

    async def _check(session: ClientSession) -> None:
        result = await session.call_tool("demo_slow", {})
        assert_that(result.isError).is_true()
        envelope = _payload(result)["error"]
        assert_that(envelope["code"]).is_equal_to("execution_error")
        assert_that(envelope["detail"]["reason"]).is_equal_to("timeout")
        assert_that(envelope["detail"]["timeout_seconds"]).is_equal_to(0.05)

    _run_session(
        workspace=tmp_path,
        registry=_failing_registry(tmp_path),
        check=_check,
    )
