# MCP Server

Lintro can expose tools to MCP-compatible agents over **stdio**.

## Install

The MCP server depends on the optional Python `mcp` SDK, kept out of the base install so
the CLI stays light:

```bash
uv pip install 'lintro[mcp]'
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
It is the root that every declared path argument is required to stay within (see
[Tool contract](#tool-contract)); it is not an OS-level sandbox around the handler
process.

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
The guard covers the arguments a tool declares; it does not sandbox whatever else a
handler chooses to touch, so toolkits must declare every path they accept.

Synchronous handlers run in a worker thread, and every call is bounded by the spec's
`timeout_seconds`, so one wedged linter cannot stall the JSON-RPC stream.

## Errors

Every tool-call failure — unknown tool, bad arguments, workspace escape, handler crash
or timeout — comes back as `isError: true` with the same envelope in both
`structuredContent` and the JSON text content. The envelope is nested under `error`, the
same outer key `lintro review --output json` uses for its (differently shaped)
provider-failure body, so it can never be confused with a tool's own successful payload:

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
`detail` is optional and may be `null`. A handler that exceeds its time budget
(`timeout_seconds`, 300s by default) reports `execution_error` with
`detail.reason == "timeout"`.

Failures outside a tool call — a missing `lintro[mcp]` extra, a transport or session
error — surface through the CLI or the MCP protocol itself rather than this envelope.

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
