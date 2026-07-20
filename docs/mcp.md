# MCP Server

Lintro can expose tools to MCP-compatible agents over **stdio**.

## Install

The MCP server depends on the optional Python `mcp` SDK:

```bash
uv pip install 'lintro[mcp]'
# or
pip install 'lintro[mcp]'
```

`lintro doctor` reports whether the `mcp` extra is available under **Optional
extras** (missing is informational, not a failure).

## Start the server

```bash
lintro mcp
lintro mcp --workspace /path/to/repo
```

`--workspace` defaults to the current working directory. All path arguments
accepted by future toolkit tools are resolved with realpath containment under
this root (symlink-escape safe).

## Built-in tool

| Tool | Annotations | Result |
| --- | --- | --- |
| `lintro_ping` | read-only, idempotent | `{status, lintro_version, workspace}` |

Additional toolkits (check, format, review, and so on) register through the
internal `McpToolRegistry` and ship in follow-up issues.

## Agent configuration example

Point your MCP host at the lintro CLI (stdio):

```json
{
  "mcpServers": {
    "lintro": {
      "command": "lintro",
      "args": ["mcp", "--workspace", "/path/to/repo"]
    }
  }
}
```

## Errors

Tool failures use a stable envelope:

```json
{
  "code": "workspace_violation",
  "message": "Path escapes workspace: ...",
  "detail": {}
}
```

Codes: `workspace_violation`, `tool_unavailable`, `invalid_input`,
`execution_error`.
