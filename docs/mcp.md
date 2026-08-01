# MCP Server

Lintro can expose tools to MCP-compatible agents over **stdio**.

## Install

The MCP server depends on the optional Python `mcp` SDK, kept out of the base install so
the CLI stays light:

```bash
uv pip install 'lintro[mcp]'
# or
pip install 'lintro[mcp]'
```

`lintro doctor` reports whether the `mcp` extra is available under **Optional extras**
(missing is informational, not a failure). Without the extra, `lintro mcp` exits with
install guidance rather than a traceback.

## Start the server

```bash
lintro mcp
lintro mcp --workspace /path/to/repo
```

`--workspace` defaults to the current working directory and is resolved once at startup.
It is the only root the server will read or write under.

## Built-in tool

| Tool          | Annotations           | Result                                |
| ------------- | --------------------- | ------------------------------------- |
| `lintro_ping` | read-only, idempotent | `{status, lintro_version, workspace}` |

Additional toolkits (check, format, review, and so on) register through the internal
`McpToolRegistry` and ship in follow-up issues.

## Tool contract

Each tool is an `McpToolSpec` with a name, description, JSON Schema for its arguments, a
handler, and three capability flags that become MCP annotation hints so an agent host
can permission-gate correctly:

| Spec flag     | MCP hint          | Meaning                           |
| ------------- | ----------------- | --------------------------------- |
| `read_only`   | `readOnlyHint`    | Does not modify the workspace     |
| `destructive` | `destructiveHint` | May irreversibly change files     |
| `idempotent`  | `idempotentHint`  | Repeated identical calls are safe |

Before any handler runs, the server:

1. Validates the raw arguments against the tool's `input_schema` (JSON Schema).
2. Resolves every property named in the spec's `path_arguments` and requires realpath
   containment under the workspace root, so a symlink inside the workspace that points
   outside cannot be used to escape and relative paths are anchored to the workspace
   rather than the server's cwd.

Handlers therefore only ever see validated arguments with absolute, in-workspace paths.

## Errors

Every failure — unknown tool, bad arguments, workspace escape, handler crash — comes
back as `isError: true` with the same envelope in both `structuredContent` and the JSON
text content. The envelope is nested under `error` to match the machine-readable error
contract `lintro review --output json` emits, and so it can never be confused with a
tool's own successful payload:

```json
{
  "error": {
    "code": "workspace_violation",
    "message": "Path escapes workspace: ../secrets",
    "detail": {
      "path": "../secrets",
      "resolved": "/tmp/secrets",
      "workspace": "/path/to/repo"
    }
  }
}
```

Codes: `workspace_violation`, `tool_unavailable`, `invalid_input`, `execution_error`.
`detail` is optional and may be `null`.

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
