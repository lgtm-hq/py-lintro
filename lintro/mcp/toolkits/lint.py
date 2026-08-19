"""The ``lintro_check`` and ``lintro_format`` MCP tools.

Both tools drive the real runner (:mod:`lintro.mcp.toolkits.runner`) and shape
their payload with the canonical serializer in :mod:`lintro.utils.findings`, so
an agent sees the same finding fields SARIF emits rather than a second,
MCP-only shape.

Only deterministic tools are reachable here. Advisory AI finders run under
``lintro review`` (issue #1886), and asking for one by name is rejected with
``tool_unavailable`` exactly as the CLI rejects it.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from lintro.enums.action import Action
from lintro.mcp.registry import McpToolSpec
from lintro.mcp.toolkits.runner import (
    resolve_tools_to_run,
    run_lintro,
    workspace_session,
)
from lintro.mcp.toolkits.snapshot import changes_since, restore, snapshot_files
from lintro.utils.findings import (
    FindingScope,
    findings_from_results,
    tool_summaries_from_results,
)

if TYPE_CHECKING:
    from lintro.models.core.run_artifact import RunArtifact

__all__ = ["build_lint_toolkit"]

_PATHS_SCHEMA: Final[dict[str, Any]] = {
    "type": "array",
    "items": {"type": "string"},
    "description": (
        "Files or directories to scan, relative to the workspace root or "
        "absolute within it. Defaults to the whole workspace."
    ),
}

_TOOLS_SCHEMA: Final[dict[str, Any]] = {
    "type": "array",
    "items": {"type": "string"},
    "description": (
        "Subset of lintro tools to run (e.g. ['ruff', 'prettier']). Omit to "
        "use the workspace config or, with no config, the language-detected "
        "toolset. Pass ['all'] for the full registry. Advisory AI finders "
        "are not selectable here."
    ),
}

_CHECK_INPUT_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {"paths": _PATHS_SCHEMA, "tools": _TOOLS_SCHEMA},
    "additionalProperties": False,
}

_FORMAT_INPUT_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "paths": _PATHS_SCHEMA,
        "tools": _TOOLS_SCHEMA,
        "dry_run": {
            "type": "boolean",
            "default": True,
            "description": (
                "When true (the default) the workspace is left untouched and "
                "the unified diff of every change is returned. Set false to "
                "write the changes."
            ),
        },
    },
    "additionalProperties": False,
}

_CHECK_DESCRIPTION: Final[str] = (
    "Run lintro's deterministic linters over the workspace and return "
    "structured findings (file, line, column, rule, severity, message, "
    "fixable) plus a per-tool summary. Read-only: no file is modified."
)

_FORMAT_DESCRIPTION: Final[str] = (
    "Run lintro's formatters. Defaults to dry_run=true, which reports the "
    "unified diff of every change without writing anything; call with "
    "dry_run=false to apply them. Returns the changed files, their diffs, "
    "the findings that remain, and a per-tool summary."
)


def _resolve_paths(*, arguments: dict[str, Any], workspace: Path) -> list[str]:
    """Resolve the scan targets for a call.

    The server has already resolved and workspace-checked every entry of
    ``paths`` (it is declared in ``path_arguments``), so this only supplies the
    default.

    Args:
        arguments: Validated tool arguments.
        workspace: Workspace root.

    Returns:
        list[str]: Absolute scan targets.
    """
    paths = arguments.get("paths") or []
    return [str(path) for path in paths] or [str(workspace)]


def _requested_tools(*, arguments: dict[str, Any]) -> list[str] | None:
    """Extract the requested tool subset.

    Args:
        arguments: Validated tool arguments.

    Returns:
        list[str] | None: The requested tools, or ``None`` for the default set.
    """
    tools = arguments.get("tools") or []
    return [str(tool) for tool in tools] or None


def _run_payload(
    *,
    artifact: RunArtifact,
    action: Action,
    scope: FindingScope,
) -> dict[str, Any]:
    """Build the findings/summary block shared by both tools.

    Args:
        artifact: The completed run.
        action: The action the run performed.
        scope: Which population of a fix run's issues to report.

    Returns:
        dict[str, Any]: Mapping with ``findings``, ``tools``, and ``summary``.
    """
    findings = findings_from_results(
        results=artifact.tool_results,
        action=action,
        scope=scope,
    )
    summaries = tool_summaries_from_results(
        results=artifact.tool_results,
        action=action,
        scope=scope,
    )
    return {
        "findings": [finding.to_dict() for finding in findings],
        "tools": [summary.to_dict() for summary in summaries],
        "summary": {
            "total_findings": len(findings),
            "tools_run": len(summaries),
            "success": artifact.exit_code == 0,
        },
    }


def _check_handler(
    *,
    workspace: Path,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Build the ``lintro_check`` handler bound to ``workspace``.

    Args:
        workspace: Workspace root the run is anchored to.

    Returns:
        Callable: A handler accepting an arguments dict.
    """

    def handler(arguments: dict[str, Any]) -> dict[str, Any]:
        paths = _resolve_paths(arguments=arguments, workspace=workspace)
        tools = _requested_tools(arguments=arguments)
        with workspace_session(workspace=workspace):
            # Selection is resolved up front because ``execute_run`` folds a
            # bad tool name into a console message and an early-exit artifact,
            # which would reach the agent as "a run with no findings".
            resolve_tools_to_run(tools=tools, action=Action.CHECK)
            artifact = run_lintro(action=Action.CHECK, paths=paths, tools=tools)
        return _run_payload(
            artifact=artifact,
            action=Action.CHECK,
            scope=FindingScope.ALL,
        )

    return handler


def _format_handler(
    *,
    workspace: Path,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Build the ``lintro_format`` handler bound to ``workspace``.

    Args:
        workspace: Workspace root the run is anchored to.

    Returns:
        Callable: A handler accepting an arguments dict.
    """

    def handler(arguments: dict[str, Any]) -> dict[str, Any]:
        paths = _resolve_paths(arguments=arguments, workspace=workspace)
        tools = _requested_tools(arguments=arguments)
        dry_run = bool(arguments.get("dry_run", True))

        with workspace_session(workspace=workspace):
            to_run = resolve_tools_to_run(tools=tools, action=Action.FIX)
            snapshot = snapshot_files(paths=paths, tool_names=to_run)
            try:
                artifact = run_lintro(action=Action.FIX, paths=paths, tools=tools)
                changes = changes_since(snapshot=snapshot, workspace=workspace)
            finally:
                if dry_run:
                    restore(snapshot=snapshot)

        payload = _run_payload(
            artifact=artifact,
            action=Action.FIX,
            scope=FindingScope.REMAINING,
        )
        payload["dry_run"] = dry_run
        payload["changed_files"] = [change.path for change in changes]
        payload["diffs"] = [change.to_dict() for change in changes]
        return payload

    return handler


def build_lint_toolkit(*, workspace: Path) -> tuple[McpToolSpec, ...]:
    """Build the check/format tool specifications.

    Args:
        workspace: Workspace root the tools operate on.

    Returns:
        tuple[McpToolSpec, ...]: ``lintro_check`` and ``lintro_format``.
    """
    return (
        McpToolSpec(
            name="lintro_check",
            description=_CHECK_DESCRIPTION,
            input_schema=_CHECK_INPUT_SCHEMA,
            handler=_check_handler(workspace=workspace),
            read_only=True,
            destructive=False,
            idempotent=True,
            path_arguments=("paths",),
        ),
        McpToolSpec(
            name="lintro_format",
            description=_FORMAT_DESCRIPTION,
            input_schema=_FORMAT_INPUT_SCHEMA,
            handler=_format_handler(workspace=workspace),
            read_only=False,
            destructive=True,
            idempotent=False,
            path_arguments=("paths",),
        ),
    )
