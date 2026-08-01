"""Structured MCP error envelopes and workspace path guards.

The envelope is nested under an ``error`` key so it mirrors the machine-readable
JSON error contract the ``review`` command emits
(:mod:`lintro.ai.review.error_contract`) and can never be confused with a
successful tool payload, which is an arbitrary tool-defined object::

    {"error": {"code": "workspace_violation", "message": "...", "detail": {...}}}
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lintro.mcp.enums.mcp_error_code import McpErrorCode

__all__ = [
    "McpError",
    "McpErrorCode",
    "McpErrorEnvelope",
    "ensure_within_workspace",
]


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
        """Serialize the envelope body to a JSON-compatible dict.

        Returns:
            Dict with ``code``, ``message``, and ``detail`` keys.
        """
        return {
            "code": self.code.value,
            "message": self.message,
            "detail": self.detail,
        }

    def to_payload(self) -> dict[str, Any]:
        """Serialize the envelope to the wire payload agents receive.

        Returns:
            Dict of the form ``{"error": {code, message, detail}}``.
        """
        return {"error": self.to_dict()}


class McpError(Exception):
    """Exception carrying a structured :class:`McpErrorEnvelope`."""

    def __init__(
        self,
        *,
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
        """Return the error code.

        Returns:
            The wrapped envelope's error code.
        """
        return self.envelope.code

    def to_dict(self) -> dict[str, Any]:
        """Serialize the wrapped envelope body.

        Returns:
            Dict with ``code``, ``message``, and ``detail`` keys.
        """
        return self.envelope.to_dict()

    def to_payload(self) -> dict[str, Any]:
        """Serialize the wrapped envelope to the wire payload.

        Returns:
            Dict of the form ``{"error": {code, message, detail}}``.
        """
        return self.envelope.to_payload()


def ensure_within_workspace(
    *,
    path: str | Path,
    workspace: Path,
) -> Path:
    """Resolve ``path`` and require realpath containment under ``workspace``.

    Both the candidate and the workspace root are fully resolved before the
    containment check, so a symlink *inside* the workspace that points outside
    cannot be used to escape, and a workspace root that is itself reached
    through a symlink (``/tmp`` on macOS) still matches its own children.

    Relative paths are interpreted against the workspace root rather than the
    server process cwd, so a tool argument can never be re-anchored by however
    the agent host happened to launch ``lintro mcp``.

    Args:
        path: Absolute or workspace-relative path to validate.
        workspace: Workspace root directory.

    Returns:
        The resolved absolute path when it is inside the workspace.

    Raises:
        McpError: :attr:`McpErrorCode.INVALID_INPUT` when ``path`` is empty or
            not a usable filesystem path, or
            :attr:`McpErrorCode.WORKSPACE_VIOLATION` when the resolved path
            escapes the workspace root.
    """
    raw = str(path)
    if not raw.strip():
        raise McpError(
            code=McpErrorCode.INVALID_INPUT,
            message="Path must be a non-empty string",
            detail={"path": raw},
        )

    workspace_root = workspace.resolve()
    try:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = workspace_root / candidate
        resolved = candidate.resolve()
    except (OSError, ValueError) as exc:
        raise McpError(
            code=McpErrorCode.INVALID_INPUT,
            message=f"Path could not be resolved: {raw}",
            detail={"path": raw, "reason": str(exc)},
        ) from exc

    if not resolved.is_relative_to(workspace_root):
        raise McpError(
            code=McpErrorCode.WORKSPACE_VIOLATION,
            message=f"Path escapes workspace: {raw}",
            detail={
                "path": raw,
                "resolved": str(resolved),
                "workspace": str(workspace_root),
            },
        )
    return resolved
