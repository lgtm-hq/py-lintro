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
from functools import partial
from pathlib import Path
from typing import Any

from loguru import logger

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
    """Create a registry with the built-in tools and the lint toolkit.

    Args:
        workspace: Workspace root the tools are anchored to.

    Returns:
        Registry containing ``lintro_ping``, ``lintro_check``,
        ``lintro_format``, ``lintro_review``, ``lintro_list_tools``,
        ``lintro_versions``, and ``lintro_doctor``.
    """
    # Deferred deliberately, in the same spirit as this module's other lazy
    # imports: the lint toolkit pulls the plugin registry, the parsers, and the
    # formatters, and nothing that merely imports this module (``lintro
    # doctor``, the CLI's command table) should pay for that.
    from lintro.mcp.toolkits.introspection import build_introspection_toolkit
    from lintro.mcp.toolkits.lint import build_lint_toolkit
    from lintro.mcp.toolkits.review import build_review_toolkit

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
    registry.register_toolkit(specs=build_lint_toolkit(workspace=workspace))
    # Registered unconditionally: the review tool answers ``tool_unavailable``
    # when the ``[ai]`` extra or the provider is missing, which tells an agent
    # why it cannot review instead of silently omitting the capability.
    registry.register_toolkit(specs=build_review_toolkit(workspace=workspace))
    registry.register_toolkit(specs=build_introspection_toolkit(workspace=workspace))
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


async def _dispatch(*, spec: McpToolSpec, arguments: dict[str, Any]) -> Any:
    """Invoke a tool handler off the event loop, under a time budget.

    Coroutine handlers are awaited directly. Synchronous handlers run in a
    worker thread: toolkit handlers shell out to linters, and a blocking call
    on the event loop would stall every other in-flight tool call and the
    JSON-RPC stream itself. The worker thread is abandoned on timeout — a
    wedged subprocess cannot be interrupted from here, but the client gets its
    answer instead of hanging forever.

    Args:
        spec: The tool whose handler should run.
        arguments: Validated, path-resolved arguments.

    Note:
        Raises ``TimeoutError`` (from ``anyio.fail_after``) when the handler
        exceeds ``spec.timeout_seconds``; the caller shapes it into an
        ``execution_error`` envelope.

    Returns:
        The handler's result.
    """
    import anyio
    import anyio.to_thread

    with anyio.fail_after(spec.timeout_seconds):
        if inspect.iscoroutinefunction(spec.handler):
            return await spec.handler(arguments)

        result = await anyio.to_thread.run_sync(
            partial(spec.handler, arguments),
            abandon_on_cancel=True,
        )
        if inspect.isawaitable(result):
            return await result
        return result


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
            return await _dispatch(spec=spec, arguments=safe_arguments)
        except McpError as exc:
            return _error_result(envelope=exc.envelope)
        except TimeoutError:
            logger.warning(
                f"MCP tool {name!r} timed out after {spec.timeout_seconds}s",
            )
            return _error_result(
                envelope=McpErrorEnvelope(
                    code=McpErrorCode.EXECUTION_ERROR,
                    message=(
                        f"Tool {name} exceeded its {spec.timeout_seconds}s budget"
                    ),
                    detail={
                        "tool": name,
                        "reason": "timeout",
                        "timeout_seconds": spec.timeout_seconds,
                    },
                ),
            )
        except Exception as exc:
            logger.exception(f"Unhandled error in MCP tool {name!r}")
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
