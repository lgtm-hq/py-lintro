"""Integration tests for the lintro MCP server over a real stdio transport.

The protocol surface itself is covered on every PR by
``tests/unit/mcp/test_server_session.py`` (in-memory streams). This module adds
the one thing that cannot be faked in-process: that ``lintro mcp`` really is a
launchable stdio server for an agent host, and that it does not corrupt the
JSON-RPC stream with stray stdout. CI runs with ``--ignore=tests/integration``,
so this is a local/manual gate.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar

import pytest
from assertpy import assert_that
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

from lintro import __version__

_T = TypeVar("_T")


async def _with_mcp_session(
    *,
    workspace: Path,
    check: Callable[[ClientSession], Awaitable[_T]],
) -> _T:
    """Spawn ``python -m lintro mcp`` and run an async client callback.

    Args:
        workspace: Workspace root passed via ``--workspace``.
        check: Async callback receiving an initialized client session.

    Returns:
        Whatever ``check`` returns.
    """
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "lintro", "mcp", "--workspace", str(workspace)],
        cwd=str(workspace),
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            return await check(session)


@pytest.mark.integration
def test_mcp_stdio_server_lists_and_calls_ping(tmp_path: Path) -> None:
    """A real stdio server lists lintro_ping and answers a call."""

    async def _check(session: ClientSession) -> None:
        listed = await session.list_tools()
        assert_that([tool.name for tool in listed.tools]).contains("lintro_ping")

        result = await session.call_tool("lintro_ping", {})
        assert_that(result.isError).is_false()
        if result.structuredContent:
            payload: dict[str, object] = dict(result.structuredContent)
        else:
            block = result.content[0]
            assert isinstance(block, types.TextContent)
            payload = dict(json.loads(block.text))
        assert_that(payload["status"]).is_equal_to("ok")
        assert_that(payload["lintro_version"]).is_equal_to(__version__)
        assert_that(payload["workspace"]).is_equal_to(str(tmp_path.resolve()))

    asyncio.run(_with_mcp_session(workspace=tmp_path, check=_check))
