"""Shared in-memory MCP 2.x client helper for unit tests.

SDK 2.0 removed ``mcp.shared.memory.create_connected_server_and_client_session``.
``mcp.client.Client`` accepts a low-level ``Server`` directly and owns the
in-memory transport plus session handshake.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

from mcp.client import Client
from mcp.types import CallToolResult, TextContent

from lintro.mcp.registry import McpToolRegistry
from lintro.mcp.server import create_mcp_server

_T = TypeVar("_T")

__all__ = [
    "payload_from_result",
    "run_in_memory_client",
]


def run_in_memory_client(
    *,
    workspace: Path,
    check: Callable[[Client], Awaitable[_T]],
    registry: McpToolRegistry | None = None,
) -> _T:
    """Run ``check`` against an in-memory MCP client bound to a lintro server.

    Args:
        workspace: Workspace root for the server under test.
        check: Async callback receiving an initialized ``Client``.
        registry: Optional registry override; defaults to the built-ins.

    Returns:
        Whatever ``check`` returns.
    """
    server = create_mcp_server(workspace=workspace, registry=registry)

    async def _main() -> _T:
        async with Client(server) as client:
            return await check(client)

    return asyncio.run(_main())


def payload_from_result(result: CallToolResult) -> dict[str, Any]:
    """Extract a tool result payload and require the dual-write contract.

    Production always sets the same JSON object on ``structured_content`` and
    the text block. Tests must fail if either side is missing or they drift.

    Args:
        result: The ``CallToolResult`` returned by ``client.call_tool``.

    Returns:
        The payload the server sent.
    """
    assert result.structured_content is not None
    payload = dict(result.structured_content)
    block = result.content[0]
    assert isinstance(block, TextContent)
    assert dict(json.loads(block.text)) == payload
    return payload
