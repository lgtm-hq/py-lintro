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


def test_error_envelope_payload_is_nested_under_error() -> None:
    """The wire payload nests the envelope under an ``error`` key."""
    envelope = McpErrorEnvelope(
        code=McpErrorCode.TOOL_UNAVAILABLE,
        message="nope",
    )

    assert_that(envelope.to_payload()).is_equal_to(
        {
            "error": {
                "code": "tool_unavailable",
                "message": "nope",
                "detail": None,
            },
        },
    )


def test_mcp_error_exposes_envelope_dict() -> None:
    """McpError wraps the envelope and exposes to_dict()/to_payload()."""
    err = McpError(
        code=McpErrorCode.EXECUTION_ERROR,
        message="boom",
        detail={"tool": "x"},
    )

    assert_that(err.code).is_equal_to(McpErrorCode.EXECUTION_ERROR)
    assert_that(err.to_dict()["detail"]).is_equal_to({"tool": "x"})
    assert_that(err.to_payload()["error"]["code"]).is_equal_to("execution_error")


def test_path_guard_allows_workspace_relative_path(tmp_path: Path) -> None:
    """Paths inside the workspace resolve successfully."""
    target = tmp_path / "src" / "file.py"
    target.parent.mkdir()
    target.write_text("x = 1\n", encoding="utf-8")

    resolved = ensure_within_workspace(path="src/file.py", workspace=tmp_path)
    assert_that(resolved).is_equal_to(target.resolve())


def test_path_guard_anchors_relative_paths_to_workspace_not_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A relative path resolves against the workspace, not the process cwd."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    resolved = ensure_within_workspace(path="file.py", workspace=workspace)
    assert_that(resolved).is_equal_to((workspace / "file.py").resolve())


def test_path_guard_rejects_path_outside_workspace(tmp_path: Path) -> None:
    """Absolute paths outside the workspace raise WORKSPACE_VIOLATION."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("nope\n", encoding="utf-8")

    with pytest.raises(McpError) as exc_info:
        ensure_within_workspace(path=outside, workspace=workspace)

    assert_that(exc_info.value.code).is_equal_to(McpErrorCode.WORKSPACE_VIOLATION)
    assert_that(exc_info.value.to_dict()["code"]).is_equal_to("workspace_violation")


def test_path_guard_rejects_parent_traversal(tmp_path: Path) -> None:
    """Relative ``..`` traversal cannot climb out of the workspace."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(McpError) as exc_info:
        ensure_within_workspace(path="../secret.txt", workspace=workspace)

    assert_that(exc_info.value.code).is_equal_to(McpErrorCode.WORKSPACE_VIOLATION)


def test_path_guard_rejects_sibling_prefix_directory(tmp_path: Path) -> None:
    """A sibling whose name merely shares a prefix is not inside the root."""
    workspace = tmp_path / "repo"
    workspace.mkdir()
    sibling = tmp_path / "repo-evil"
    sibling.mkdir()

    with pytest.raises(McpError) as exc_info:
        ensure_within_workspace(path=sibling / "x.txt", workspace=workspace)

    assert_that(exc_info.value.code).is_equal_to(McpErrorCode.WORKSPACE_VIOLATION)


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
        ensure_within_workspace(
            path=escape_link / "secret.txt",
            workspace=workspace,
        )

    assert_that(exc_info.value.code).is_equal_to(McpErrorCode.WORKSPACE_VIOLATION)
    detail = exc_info.value.to_dict()["detail"]
    assert_that(detail).is_not_none()
    assert_that(detail["resolved"]).is_equal_to(str(secret.resolve()))


def test_path_guard_allows_symlinked_workspace_root(tmp_path: Path) -> None:
    """A workspace reached through a symlink still contains its own files."""
    real_root = tmp_path / "real"
    real_root.mkdir()
    (real_root / "file.py").write_text("x = 1\n", encoding="utf-8")
    linked_root = tmp_path / "linked"
    linked_root.symlink_to(real_root)

    resolved = ensure_within_workspace(path="file.py", workspace=linked_root)
    assert_that(resolved).is_equal_to((real_root / "file.py").resolve())


def test_path_guard_rejects_blank_path(tmp_path: Path) -> None:
    """An empty or whitespace-only path is invalid input, not a violation."""
    with pytest.raises(McpError) as exc_info:
        ensure_within_workspace(path="   ", workspace=tmp_path)

    assert_that(exc_info.value.code).is_equal_to(McpErrorCode.INVALID_INPUT)


def test_path_guard_rejects_unresolvable_path(tmp_path: Path) -> None:
    """A path containing a NUL byte is reported as invalid input."""
    with pytest.raises(McpError) as exc_info:
        ensure_within_workspace(path="bad\x00name", workspace=tmp_path)

    assert_that(exc_info.value.code).is_equal_to(McpErrorCode.INVALID_INPUT)
