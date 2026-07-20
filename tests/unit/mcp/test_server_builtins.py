"""Unit tests for built-in MCP registry tools (no SDK required)."""

from __future__ import annotations

from pathlib import Path

from assertpy import assert_that

from lintro import __version__
from lintro.mcp.server import build_default_registry


def test_default_registry_includes_read_only_ping(tmp_path: Path) -> None:
    """Default registry registers lintro_ping with read-only hints."""
    registry = build_default_registry(tmp_path)
    ping = registry.get("lintro_ping")
    assert_that(ping).is_not_none()
    assert ping is not None

    assert_that(ping.read_only).is_true()
    assert_that(ping.destructive).is_false()
    assert_that(ping.idempotent).is_true()
    assert_that(ping.to_annotations()["readOnlyHint"]).is_true()

    result = ping.handler({})
    assert_that(result).is_equal_to(
        {
            "status": "ok",
            "lintro_version": __version__,
            "workspace": str(tmp_path.resolve()),
        },
    )
