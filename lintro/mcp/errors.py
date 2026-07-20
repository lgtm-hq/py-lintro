"""Structured MCP error codes, envelopes, and workspace path guards."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto
from pathlib import Path
from typing import Any


class McpErrorCode(StrEnum):
    """Machine-readable MCP tool error codes.

    Values are lowercase snake_case (``auto()`` on :class:`StrEnum`).
    """

    WORKSPACE_VIOLATION = auto()
    TOOL_UNAVAILABLE = auto()
    INVALID_INPUT = auto()
    EXECUTION_ERROR = auto()


@dataclass(frozen=True)
class McpErrorEnvelope:
    """Stable error envelope shared with JSON output consumers.

    Attributes:
        code: Machine-readable error code.
        message: Human-readable summary.
        detail: Optional structured context (paths, tool name, etc.).
    """

    code: McpErrorCode
    message: str
    detail: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize the envelope to a JSON-compatible dict.

        Returns:
            Dict with ``code``, ``message``, and ``detail`` keys.
        """
        return {
            "code": self.code.value,
            "message": self.message,
            "detail": self.detail,
        }


class McpError(Exception):
    """Exception carrying a structured :class:`McpErrorEnvelope`."""

    def __init__(
        self,
        code: McpErrorCode,
        message: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Create an MCP error.

        Args:
            code: Machine-readable error code.
            message: Human-readable summary.
            detail: Optional structured context.
        """
        self.envelope = McpErrorEnvelope(code=code, message=message, detail=detail)
        super().__init__(message)

    @property
    def code(self) -> McpErrorCode:
        """Return the error code."""
        return self.envelope.code

    def to_dict(self) -> dict[str, Any]:
        """Serialize the wrapped envelope.

        Returns:
            Dict with ``code``, ``message``, and ``detail`` keys.
        """
        return self.envelope.to_dict()


def ensure_within_workspace(path: str | Path, workspace: Path) -> Path:
    """Resolve ``path`` and require realpath containment under ``workspace``.

    Uses :meth:`pathlib.Path.resolve` (follows symlinks) so a symlink inside
    the workspace that points outside cannot be used to escape the root.

    Args:
        path: Absolute or workspace-relative path to validate.
        workspace: Workspace root directory.

    Returns:
        The resolved absolute path when it is inside the workspace.

    Raises:
        McpError: With :attr:`McpErrorCode.WORKSPACE_VIOLATION` when the
            resolved path escapes the workspace root.
    """
    workspace_root = workspace.resolve()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = workspace_root / candidate
    resolved = candidate.resolve()

    if not resolved.is_relative_to(workspace_root):
        raise McpError(
            code=McpErrorCode.WORKSPACE_VIOLATION,
            message=f"Path escapes workspace: {path}",
            detail={
                "path": str(path),
                "resolved": str(resolved),
                "workspace": str(workspace_root),
            },
        )
    return resolved
