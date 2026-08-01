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

## Tools

| Tool            | Annotations                 | Result                                           |
| --------------- | --------------------------- | ------------------------------------------------ |
| `lintro_ping`   | read-only, idempotent       | `{status, lintro_version, workspace}`            |
| `lintro_check`  | read-only, idempotent       | Structured findings plus a per-tool summary      |
| `lintro_format` | destructive, not idempotent | Unified diffs, changed files, remaining findings |

Further toolkits (review and so on) register through the internal `McpToolRegistry` and
ship in follow-up issues.

### `lintro_check`

Runs lintro's deterministic linters and returns findings rather than rendered text.
Advisory AI finders are not reachable here; they run under `lintro review`, and naming
one is rejected with `tool_unavailable` exactly as the CLI rejects it.

Arguments (all optional):

| Argument | Type       | Default       | Meaning                                    |
| -------- | ---------- | ------------- | ------------------------------------------ |
| `paths`  | `string[]` | the workspace | Files or directories to scan               |
| `tools`  | `string[]` | config-driven | Subset of tools to run, for example `ruff` |

```json
{
  "findings": [
    {
      "tool": "ruff",
      "file": "/path/to/repo/bad.py",
      "line": 1,
      "column": 8,
      "rule": "F401",
      "severity": "WARNING",
      "message": "`os` imported but unused",
      "fixable": true,
      "doc_url": "https://docs.astral.sh/ruff/rules/unused-import/"
    }
  ],
  "tools": [{ "tool": "ruff", "status": "issues", "issue_count": 1, "duration": 0.47 }],
  "summary": { "total_findings": 1, "tools_run": 1, "success": false }
}
```

`status` is one of `passed`, `issues`, `skipped`, `timed_out`, `errored`; a skipped tool
also carries `skip_reason`. Findings come from the same serializer that feeds SARIF
(`lintro.utils.findings`), so the two never drift apart.

### `lintro_format`

Runs lintro's formatters. It takes the same `paths` and `tools` arguments plus
`dry_run`, which **defaults to `true`**.

A dry run reports what the formatters would write, as a real unified diff, and leaves
the tree byte-identical. It does that by snapshotting the files the selected tools can
reach, letting them actually run, diffing against the snapshot, and restoring every
changed file. Running the tools for real is what makes the diff trustworthy — the
alternatives (a partial temp copy, or a git checkpoint) either format under the wrong
configuration or only work inside a git work tree. If restoring a file ever fails, the
call reports `execution_error` with `detail.reason == "restore_failed"` rather than
quietly leaving the workspace modified.

`dry_run: false` runs the same way but keeps the writes.

```json
{
  "findings": [],
  "tools": [
    {
      "tool": "ruff",
      "status": "passed",
      "issue_count": 0,
      "duration": 0.66,
      "fixed_count": 2
    }
  ],
  "summary": { "total_findings": 0, "tools_run": 1, "success": true },
  "dry_run": true,
  "changed_files": ["bad.py"],
  "diffs": [{ "file": "bad.py", "diff": "--- a/bad.py\n+++ b/bad.py\n@@ ..." }]
}
```

`findings` here is the _residue_: what the formatters could not fix. How much they did
fix is `fixed_count` on each tool summary.

Notes and limits:

- `changed_files` and `diffs` use workspace-relative paths; a finding's `file` is
  whatever path the underlying tool reported.
- A dry run holds the candidate files in memory and refuses, with `execution_error` and
  `detail.reason == "snapshot_too_large"`, rather than running unbounded. Narrow `paths`
  or `tools`, or call with `dry_run: false`.
- Anything that would make the snapshot or the diff incomplete is an error rather than a
  quietly shortened report: `snapshot_read_failed` (a candidate could not be read, so
  the run never starts), `diff_read_failed`, and `restore_failed`.
- A tool you asked for by name that the workspace config disabled, or that conflict
  resolution dropped, comes back as `tool_unavailable` with `detail.skipped` rather than
  as a run that quietly did less than you asked.
- Both tools honor the workspace's `.lintro-config.yaml`. There is no MCP-only
  configuration surface.
- Runs are serialized: lintro resolves configuration relative to the process working
  directory, so one lint run happens at a time per server.

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
