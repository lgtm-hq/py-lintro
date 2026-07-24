"""CLI command to start the lintro MCP stdio server."""

from __future__ import annotations

from pathlib import Path

import click

from lintro.mcp import require_mcp


@click.command("mcp")
@click.option(
    "--workspace",
    "workspace",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Workspace root for path guards (default: current working directory).",
)
def mcp_command(workspace: Path | None) -> None:
    """Start the lintro MCP server on stdio.

    Requires the optional ``lintro[mcp]`` extra. Agents connect over stdio and
    discover tools such as ``lintro_ping``.

    Args:
        workspace: Workspace root directory; defaults to the process cwd.

    Examples:
        lintro mcp
        lintro mcp --workspace /path/to/repo
    """
    require_mcp()
    from lintro.mcp.server import run_stdio_server

    root = (workspace or Path.cwd()).resolve()
    run_stdio_server(workspace=root)
