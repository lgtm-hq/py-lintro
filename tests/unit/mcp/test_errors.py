"""Unit tests for MCP error envelopes and workspace path guards."""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.mcp.errors import (
    McpError,
    McpErrorCode,
    McpErrorEnvelope,
    ensure_within_workspace,
)


def test_error_code_auto_values_are_snake_case() -> None:
    """StrEnum auto() yields lowercase snake_case code values."""
    assert_that(McpErrorCode.WORKSPACE_VIOLATION.value).is_equal_to(
        "workspace_violation",
    )
    assert_that(McpErrorCode.TOOL_UNAVAILABLE.value).is_equal_to("tool_unavailable")
    assert_that(McpErrorCode.INVALID_INPUT.value).is_equal_to("invalid_input")
    assert_that(McpErrorCode.EXECUTION_ERROR.value).is_equal_to("execution_error")


def test_error_envelope_to_dict_shape() -> None:
    """Envelope serializes to {code, message, detail}."""
    envelope = McpErrorEnvelope(
        code=McpErrorCode.INVALID_INPUT,
        message="bad arg",
        detail={"field": "path"},
    )

    assert_that(envelope.to_dict()).is_equal_to(
        {
            "code": "invalid_input",
            "message": "bad arg",
            "detail": {"field": "path"},
        },
    )


def test_mcp_error_exposes_envelope_dict() -> None:
    """McpError wraps the envelope and exposes to_dict()."""
    err = McpError(
        code=McpErrorCode.EXECUTION_ERROR,
        message="boom",
        detail={"tool": "x"},
    )

    assert_that(err.code).is_equal_to(McpErrorCode.EXECUTION_ERROR)
    assert_that(err.to_dict()["detail"]).is_equal_to({"tool": "x"})


def test_path_guard_allows_workspace_relative_path(tmp_path: Path) -> None:
    """Paths inside the workspace resolve successfully."""
    target = tmp_path / "src" / "file.py"
    target.parent.mkdir()
    target.write_text("x = 1\n", encoding="utf-8")

    resolved = ensure_within_workspace("src/file.py", tmp_path)
    assert_that(resolved).is_equal_to(target.resolve())


def test_path_guard_rejects_path_outside_workspace(tmp_path: Path) -> None:
    """Absolute paths outside the workspace raise WORKSPACE_VIOLATION."""
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("nope\n", encoding="utf-8")

    with pytest.raises(McpError) as exc_info:
        ensure_within_workspace(outside, tmp_path)

    assert_that(exc_info.value.code).is_equal_to(McpErrorCode.WORKSPACE_VIOLATION)
    assert_that(exc_info.value.to_dict()["code"]).is_equal_to("workspace_violation")


def test_path_guard_rejects_symlink_escape(tmp_path: Path) -> None:
    """Symlinks that resolve outside the workspace are rejected."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    secret = outside_dir / "secret.txt"
    secret.write_text("secret\n", encoding="utf-8")

    escape_link = workspace / "escape"
    escape_link.symlink_to(outside_dir)

    with pytest.raises(McpError) as exc_info:
        ensure_within_workspace(escape_link / "secret.txt", workspace)

    assert_that(exc_info.value.code).is_equal_to(McpErrorCode.WORKSPACE_VIOLATION)
    detail = exc_info.value.to_dict()["detail"]
    assert_that(detail).is_not_none()
    assert_that(detail["resolved"]).is_equal_to(str(secret.resolve()))
