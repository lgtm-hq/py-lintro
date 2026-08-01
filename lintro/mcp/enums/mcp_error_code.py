"""Machine-readable error codes for MCP tool failures."""

from __future__ import annotations

from enum import StrEnum, auto


class McpErrorCode(StrEnum):
    """Machine-readable MCP tool error codes.

    Values are lowercase snake_case (``auto()`` on :class:`StrEnum`) and form
    the stable vocabulary agent hosts branch on. Adding a member is a
    backwards-compatible change; renaming one is not.

    Attributes:
        WORKSPACE_VIOLATION: A path argument resolved outside the workspace.
        TOOL_UNAVAILABLE: The requested tool is not registered on this server.
        INVALID_INPUT: Arguments failed JSON Schema or path validation.
        EXECUTION_ERROR: The tool handler raised an unexpected exception.
    """

    WORKSPACE_VIOLATION = auto()
    TOOL_UNAVAILABLE = auto()
    INVALID_INPUT = auto()
    EXECUTION_ERROR = auto()
