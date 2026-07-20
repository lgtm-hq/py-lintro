"""Stdio MCP server scaffold for lintro.

Business logic lives in toolkit handlers registered on
:class:`~lintro.mcp.registry.McpToolRegistry`. This module only wires
transport, listing, dispatch, and error shaping.
"""

# mypy: disable-error-code="untyped-decorator,no-untyped-call"

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lintro import __version__
from lintro.mcp.errors import McpError, McpErrorCode, McpErrorEnvelope
from lintro.mcp.registry import McpToolRegistry, McpToolSpec

_EMPTY_OBJECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


def _ping_handler(workspace: Path) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Build the built-in ``lintro_ping`` handler bound to ``workspace``."""

    def handler(_arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "ok",
            "lintro_version": __version__,
            "workspace": str(workspace.resolve()),
        }

    return handler


def build_default_registry(workspace: Path) -> McpToolRegistry:
    """Create a registry with the built-in smoke tool.

    Args:
        workspace: Workspace root exposed by ``lintro_ping``.

    Returns:
        Registry containing ``lintro_ping``.
    """
    registry = McpToolRegistry()
    registry.register(
        McpToolSpec(
            name="lintro_ping",
            description=(
                "Return lintro MCP server health, package version, and workspace root."
            ),
            input_schema=dict(_EMPTY_OBJECT_SCHEMA),
            handler=_ping_handler(workspace),
            read_only=True,
            destructive=False,
            idempotent=True,
        ),
    )
    return registry


def _error_result(envelope: McpErrorEnvelope) -> Any:
    """Build an MCP ``CallToolResult`` marking a structured error."""
    import mcp.types as types

    return types.CallToolResult(
        isError=True,
        content=[
            types.TextContent(
                type="text",
                text=json.dumps(envelope.to_dict()),
            ),
        ],
    )


def create_mcp_server(
    workspace: Path,
    registry: McpToolRegistry | None = None,
) -> Any:
    """Create an MCP ``Server`` rooted at ``workspace``.

    Args:
        workspace: Workspace root for path guards and ``lintro_ping``.
        registry: Optional pre-built registry; defaults to built-in tools.

    Returns:
        Configured ``mcp.server.Server`` instance.
    """
    import mcp.types as types
    from mcp.server import Server

    workspace_root = workspace.resolve()
    tool_registry = registry or build_default_registry(workspace_root)
    server = Server("lintro")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        tools: list[types.Tool] = []
        for spec in tool_registry.list_tools():
            hints = spec.to_annotations()
            tools.append(
                types.Tool(
                    name=spec.name,
                    description=spec.description,
                    inputSchema=spec.input_schema,
                    annotations=types.ToolAnnotations(
                        readOnlyHint=hints["readOnlyHint"],
                        destructiveHint=hints["destructiveHint"],
                        idempotentHint=hints["idempotentHint"],
                    ),
                ),
            )
        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> Any:
        spec = tool_registry.get(name)
        if spec is None:
            return _error_result(
                McpErrorEnvelope(
                    code=McpErrorCode.TOOL_UNAVAILABLE,
                    message=f"Unknown MCP tool: {name}",
                    detail={"tool": name},
                ),
            )

        try:
            result = spec.handler(arguments or {})
            if inspect.isawaitable(result):
                result = await result
            return result
        except McpError as exc:
            return _error_result(exc.envelope)
        except Exception as exc:
            return _error_result(
                McpErrorEnvelope(
                    code=McpErrorCode.EXECUTION_ERROR,
                    message=str(exc) or exc.__class__.__name__,
                    detail={"tool": name},
                ),
            )

    # Attach for tests / introspection without using private MCP internals.
    server.lintro_workspace = workspace_root  # type: ignore[attr-defined]
    server.lintro_registry = tool_registry  # type: ignore[attr-defined]
    return server


async def run_stdio_server_async(
    workspace: Path,
    registry: McpToolRegistry | None = None,
) -> None:
    """Run the lintro MCP server over stdio (async).

    Args:
        workspace: Workspace root directory.
        registry: Optional tool registry override.
    """
    from mcp.server.stdio import stdio_server

    server = create_mcp_server(workspace=workspace, registry=registry)
    init_options = server.create_initialization_options()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, init_options)


def run_stdio_server(
    workspace: Path,
    registry: McpToolRegistry | None = None,
) -> None:
    """Run the lintro MCP server over stdio (blocking).

    Args:
        workspace: Workspace root directory.
        registry: Optional tool registry override.
    """
    import anyio

    anyio.run(run_stdio_server_async, workspace, registry)
