"""Run the real lintro pipeline on behalf of an MCP tool call.

Both MCP lint tools execute the same runner the CLI uses — ``build_run_context``
plus ``execute_run`` (the execute half of the issue #1823 split) — rather than
re-deriving tool selection, configuration, or aggregation. The render half is
deliberately *not* called: an MCP server owns stdout for the JSON-RPC stream,
so nothing may print a document there.

Three concerns are handled here that a plain CLI invocation gets for free:

* **stdout hygiene** — the context is built with ``output_format="json"``,
  which routes all decorative console output to stderr.
* **workspace anchoring** — lintro resolves ``.lintro-config.yaml`` and its
  tool configs relative to the process cwd, so a run happens with the cwd
  chdir'd to the server's workspace root and the config cache cleared, which
  is what "respect the workspace's ``.lintro-config.yaml``" means in practice.
* **serialization** — cwd is process-global and the config cache is a module
  singleton, so concurrent tool calls would corrupt each other.
  :func:`workspace_session` holds one lock for the whole call, which for
  ``lintro_format`` spans snapshot, run, diff, and restore rather than just
  the run. MCP tools that do not touch lintro (``lintro_ping``) are
  unaffected.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from lintro.mcp.enums.mcp_error_code import McpErrorCode
from lintro.mcp.errors import McpError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from lintro.enums.action import Action
    from lintro.models.core.run_artifact import RunArtifact

__all__ = ["RUN_LOCK", "resolve_tools_to_run", "run_lintro", "workspace_session"]

# Serializes lintro work across MCP tool calls. See the module docstring.
RUN_LOCK: threading.Lock = threading.Lock()

# Per-process run-log directory for MCP runs. A CLI run drops its logs in the
# workspace's ``.lintro/``; doing that here would make ``lintro_check`` write
# to a tree it advertises as read-only, and would leave a dry-run preview with
# a visible footprint. One directory is reused for the life of the server
# rather than one per call, because loguru's file sinks outlive a single run
# and would be left pointing at a deleted path.
_LOG_DIR: Path | None = None


def _mcp_log_dir() -> str:
    """Return (creating once) the process-wide run-log directory for MCP runs.

    Returns:
        str: Path to the directory ``LINTRO_LOG_DIR`` should point at.
    """
    global _LOG_DIR
    if _LOG_DIR is None:
        _LOG_DIR = Path(tempfile.mkdtemp(prefix="lintro-mcp-"))
    return str(_LOG_DIR)


@contextlib.contextmanager
def _log_dir_env() -> Iterator[None]:
    """Point ``LINTRO_LOG_DIR`` at the MCP run-log directory for one session.

    The variable is restored on exit rather than left set: the environment is
    process-global, and a library that permanently redirected it would follow
    every other lintro caller in the same process.

    Yields:
        None: Control, with the variable in place.
    """
    previous = os.environ.get("LINTRO_LOG_DIR")
    if previous is None:
        # An operator who set LINTRO_LOG_DIR explicitly still wins.
        os.environ["LINTRO_LOG_DIR"] = _mcp_log_dir()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("LINTRO_LOG_DIR", None)


@contextlib.contextmanager
def workspace_session(*, workspace: Path) -> Iterator[None]:
    """Hold the run lock with the process anchored at ``workspace``.

    The lock is not reentrant, so the functions below assume the session is
    already open rather than acquiring it themselves.

    Args:
        workspace: Workspace root to make the process cwd for the duration.

    Yields:
        None: Control, with cwd, log directory, and config cache prepared.
    """
    from lintro.config.config_loader import clear_config_cache

    with RUN_LOCK, contextlib.chdir(workspace), _log_dir_env():
        clear_config_cache()
        try:
            yield
        finally:
            # The cache is a module singleton keyed by nothing, so leaving the
            # workspace's configuration in it would follow the next in-process
            # ``get_config()`` caller back out to whatever cwd they run under.
            clear_config_cache()


def resolve_tools_to_run(*, tools: list[str] | None, action: Action) -> list[str]:
    """Resolve the tool names a run would execute, validating any subset.

    Must be called inside :func:`workspace_session`, because tool selection
    reads the workspace configuration.

    Args:
        tools: Requested tool names, or ``None``/empty for the default set.
        action: The action the run will perform, which decides whether a tool
            that cannot fix is acceptable.

    Returns:
        list[str]: Tool names the run will execute, in execution order.

    Raises:
        McpError: :attr:`McpErrorCode.TOOL_UNAVAILABLE` when a requested tool
            is unknown, is an advisory AI finder (which runs under
            ``lintro review`` rather than ``chk``/``fmt``, per issue #1886),
            cannot format, or would be skipped (disabled in config, or dropped
            by conflict resolution). A skip is only an error for an explicitly
            requested tool: the default set legitimately skips whatever the
            workspace turned off, and the run reports those skips itself.
    """
    from lintro.utils.execution.tool_configuration import get_tools_to_run

    requested = ",".join(tools) if tools else None
    try:
        selection = get_tools_to_run(requested, action, ignore_conflicts=False)
    except ValueError as exc:
        raise McpError(
            code=McpErrorCode.TOOL_UNAVAILABLE,
            message=str(exc),
            detail={"tools": list(tools or []), "action": str(action)},
        ) from exc

    if tools and selection.skipped:
        # ``execute_run`` records a skipped tool as a successful result, so a
        # partial selection would come back as "success, no findings" for a
        # tool the caller explicitly asked for.
        skipped = {tool.name: tool.reason for tool in selection.skipped}
        raise McpError(
            code=McpErrorCode.TOOL_UNAVAILABLE,
            message=(
                "Requested tools were not run: "
                + ", ".join(f"{name} ({reason})" for name, reason in skipped.items())
            ),
            detail={
                "tools": list(tools),
                "action": str(action),
                "skipped": skipped,
            },
        )
    return list(selection.to_run)


def run_lintro(
    *,
    action: Action,
    paths: list[str],
    tools: list[str] | None,
) -> RunArtifact:
    """Execute one lintro run and return its artifact.

    Must be called inside :func:`workspace_session`.

    Args:
        action: Action to perform.
        paths: Absolute, already workspace-checked scan targets.
        tools: Requested tool subset, or ``None`` for the default set.

    Returns:
        RunArtifact: The completed run's results, totals, and exit code.
    """
    from lintro.utils.tool_executor import build_run_context, execute_run

    ctx = build_run_context(
        action=action,
        output_format="json",
        score=False,
        debug=False,
        no_art=True,
        dry_run=False,
    )
    return execute_run(
        ctx=ctx,
        paths=paths,
        tools=",".join(tools) if tools else None,
        tool_options=None,
        exclude=None,
        include_venv=False,
        group_by="auto",
        output_format="json",
        verbose=False,
        raw_output=False,
        incremental=False,
        auto_install=False,
        yes=True,
        ignore_conflicts=False,
        fail_under=None,
        diff_base=None,
        ai_status_lines=None,
        on_tool_result=None,
    )
