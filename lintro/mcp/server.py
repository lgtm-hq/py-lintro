"""Stdio MCP server scaffold for lintro.

Business logic lives in toolkit handlers registered on
:class:`~lintro.mcp.registry.McpToolRegistry`. This module only wires
transport, listing, argument validation, workspace-boundary enforcement,
dispatch, and error shaping.

Heavy imports (the ``mcp`` SDK, ``anyio``) are deliberately deferred into the
functions that need them so importing :mod:`lintro.mcp` — which the ``doctor``
command and the CLI do — never pulls the optional stack.
"""

# mypy: disable-error-code="untyped-decorator,no-untyped-call"

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lintro.mcp.enums.mcp_error_code import McpErrorCode
from lintro.mcp.errors import McpError, McpErrorEnvelope
from lintro.mcp.registry import McpToolRegistry, McpToolSpec
from lintro.mcp.validation import resolve_path_arguments, validate_arguments

__all__ = [
    "build_default_registry",
    "create_mcp_server",
    "run_stdio_server",
    "run_stdio_server_async",
]

_EMPTY_OBJECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


def _ping_handler(*, workspace: Path) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Build the built-in ``lintro_ping`` handler bound to ``workspace``.

    Args:
        workspace: Workspace root reported back to the caller.

    Returns:
        A handler callable accepting an arguments dict.
    """

    def handler(_arguments: dict[str, Any]) -> dict[str, Any]:
        from lintro import __version__

        return {
            "status": "ok",
            "lintro_version": __version__,
            "workspace": str(workspace.resolve()),
        }

    return handler


def build_default_registry(*, workspace: Path) -> McpToolRegistry:
    """Create a registry with the built-in smoke tool.

    Args:
        workspace: Workspace root exposed by ``lintro_ping``.

    Returns:
        Registry containing ``lintro_ping``.
    """
    registry = McpToolRegistry()
    registry.register(
        spec=McpToolSpec(
            name="lintro_ping",
            description=(
                "Return lintro MCP server health, package version, and workspace root."
            ),
            input_schema=dict(_EMPTY_OBJECT_SCHEMA),
            handler=_ping_handler(workspace=workspace),
            read_only=True,
            destructive=False,
            idempotent=True,
        ),
    )
    return registry


def _error_result(*, envelope: McpErrorEnvelope) -> Any:
    """Build an MCP ``CallToolResult`` marking a structured error.

    Args:
        envelope: The structured error to report.

    Returns:
        A ``mcp.types.CallToolResult`` with ``isError`` set, carrying the
        envelope as both structured content and JSON text.
    """
    import mcp.types as types

    payload = envelope.to_payload()
    return types.CallToolResult(
        isError=True,
        structuredContent=payload,
        content=[
            types.TextContent(
                type="text",
                text=json.dumps(payload, indent=2),
            ),
        ],
    )


def create_mcp_server(
    *,
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
    tool_registry = registry or build_default_registry(workspace=workspace_root)
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

    # validate_input=False: the SDK's built-in schema check reports failures as
    # opaque prose, which would bypass the structured envelope this server
    # promises. Validation happens below so every failure mode — unknown tool,
    # bad arguments, workspace escape, handler crash — shares one shape.
    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> Any:
        spec = tool_registry.get(name=name)
        if spec is None:
            return _error_result(
                envelope=McpErrorEnvelope(
                    code=McpErrorCode.TOOL_UNAVAILABLE,
                    message=f"Unknown MCP tool: {name}",
                    detail={"tool": name},
                ),
            )

        try:
            raw_arguments = arguments or {}
            validate_arguments(spec=spec, arguments=raw_arguments)
            safe_arguments = resolve_path_arguments(
                spec=spec,
                arguments=raw_arguments,
                workspace=workspace_root,
            )
            result = spec.handler(safe_arguments)
            if inspect.isawaitable(result):
                result = await result
            return result
        except McpError as exc:
            return _error_result(envelope=exc.envelope)
        except Exception as exc:
            return _error_result(
                envelope=McpErrorEnvelope(
                    code=McpErrorCode.EXECUTION_ERROR,
                    message=str(exc) or exc.__class__.__name__,
                    detail={"tool": name},
                ),
            )

    return server


async def run_stdio_server_async(
    workspace: Path,
    registry: McpToolRegistry | None = None,
) -> None:
    """Run the lintro MCP server over stdio (async).

    Positional parameters are required by ``anyio.run``, which passes task
    arguments positionally.

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
    *,
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
