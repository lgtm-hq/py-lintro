"""Unit tests for optional MCP SDK availability helpers."""

from __future__ import annotations

import builtins
import sys
from unittest.mock import patch

import pytest
from assertpy import assert_that
from click import UsageError

from lintro.mcp import is_mcp_available, require_mcp


def test_is_mcp_available_true_when_importable() -> None:
    """Availability is true when import mcp succeeds."""
    with patch.dict("sys.modules", {"mcp": object()}):
        assert_that(is_mcp_available()).is_true()


def test_is_mcp_available_false_on_import_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Availability is false when mcp cannot be imported."""
    for key in [k for k in sys.modules if k == "mcp" or k.startswith("mcp.")]:
        monkeypatch.delitem(sys.modules, key, raising=False)

    original_import = builtins.__import__

    def _fake_import(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "mcp" or name.startswith("mcp."):
            raise ImportError("no mcp")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    assert_that(is_mcp_available()).is_false()


def test_require_mcp_raises_usage_error_when_missing() -> None:
    """require_mcp raises a Click UsageError with install guidance."""
    with (
        patch("lintro.mcp.is_mcp_available", return_value=False),
        pytest.raises(UsageError) as exc_info,
    ):
        require_mcp()

    assert_that(str(exc_info.value)).contains("lintro[mcp]")
