"""The ``tools/list`` freshness hint the lintro server advertises (#2577)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from assertpy import assert_that
from mcp.client import Client

from lintro.mcp.server import DEFAULT_TOOL_LIST_TTL_SECONDS, create_mcp_server
from tests.unit.mcp.session_helpers import run_in_memory_client


def test_default_tool_list_ttl_is_one_minute() -> None:
    """The documented default is 60 seconds."""
    assert_that(DEFAULT_TOOL_LIST_TTL_SECONDS).is_equal_to(60.0)


def test_tools_list_carries_the_default_ttl(tmp_path: Path) -> None:
    """A listed tool set is fresh for the default TTL, in milliseconds on the wire.

    Args:
        tmp_path: Workspace root for the server under test.
    """

    async def check(client: Client) -> tuple[int, str]:
        result = await client.list_tools()
        return result.ttl_ms, result.cache_scope

    ttl_ms, scope = run_in_memory_client(workspace=tmp_path, check=check)

    assert_that(ttl_ms).is_equal_to(int(DEFAULT_TOOL_LIST_TTL_SECONDS * 1000))
    assert_that(scope).is_equal_to("private")


def test_tools_list_ttl_is_configurable(tmp_path: Path) -> None:
    """A caller may shorten or disable the hint.

    Args:
        tmp_path: Workspace root for the server under test.
    """
    server = create_mcp_server(workspace=tmp_path, tool_list_ttl_seconds=0)

    async def list_ttl() -> int:
        async with Client(server) as client:
            return (await client.list_tools()).ttl_ms

    assert_that(asyncio.run(list_ttl())).is_equal_to(0)


def test_negative_tool_list_ttl_is_rejected(tmp_path: Path) -> None:
    """A negative TTL is a caller bug, refused at construction.

    Args:
        tmp_path: Workspace root for the server under test.
    """
    with pytest.raises(ValueError, match="tool_list_ttl_seconds"):
        create_mcp_server(workspace=tmp_path, tool_list_ttl_seconds=-1)


def test_server_announces_lintros_version(tmp_path: Path) -> None:
    """``serverInfo.version`` is lintro's own version, not the SDK's empty default.

    Args:
        tmp_path: Workspace root for the server under test.
    """
    from lintro import __version__

    server = create_mcp_server(workspace=tmp_path)

    assert_that(server.version).is_equal_to(__version__)
