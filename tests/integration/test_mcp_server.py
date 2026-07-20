"""Integration tests for the lintro MCP stdio server."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from assertpy import assert_that

mcp = pytest.importorskip(
    "mcp",
    reason="optional lintro[mcp] extra not installed; run: uv sync --extra mcp",
)

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

from lintro import __version__  # noqa: E402


async def _with_mcp_session(workspace: Path, coro_factory):  # type: ignore[no-untyped-def]
    """Spawn ``python -m lintro mcp`` and run an async client callback."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "lintro", "mcp", "--workspace", str(workspace)],
        cwd=str(workspace),
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            return await coro_factory(session)


@pytest.mark.integration
def test_mcp_tools_list_includes_lintro_ping_with_hints(
    tmp_path: Path,
) -> None:
    """tools/list exposes lintro_ping with read-only annotation hints."""

    async def _check(session: ClientSession) -> None:
        listed = await session.list_tools()
        names = [tool.name for tool in listed.tools]
        assert_that(names).contains("lintro_ping")

        ping = next(tool for tool in listed.tools if tool.name == "lintro_ping")
        assert_that(ping.annotations).is_not_none()
        annotations = ping.annotations
        assert annotations is not None
        assert_that(annotations.readOnlyHint).is_true()
        assert_that(annotations.destructiveHint).is_false()
        assert_that(annotations.idempotentHint).is_true()

    asyncio.run(_with_mcp_session(tmp_path, _check))


@pytest.mark.integration
def test_mcp_call_lintro_ping_returns_server_info(tmp_path: Path) -> None:
    """Calling lintro_ping returns status, version, and workspace."""

    async def _check(session: ClientSession) -> None:
        result = await session.call_tool("lintro_ping", {})
        assert_that(result.isError).is_false()

        payload: dict[str, object]
        if result.structuredContent:
            payload = dict(result.structuredContent)
        else:
            text = result.content[0].text  # type: ignore[union-attr]
            payload = json.loads(text)

        assert_that(payload["status"]).is_equal_to("ok")
        assert_that(payload["lintro_version"]).is_equal_to(__version__)
        assert_that(payload["workspace"]).is_equal_to(str(tmp_path.resolve()))

    asyncio.run(_with_mcp_session(tmp_path, _check))
