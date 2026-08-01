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

| Tool                | Annotations                 | Result                                                                         |
| ------------------- | --------------------------- | ------------------------------------------------------------------------------ |
| `lintro_ping`       | read-only, idempotent       | `{status, lintro_version, workspace}`                                          |
| `lintro_check`      | read-only, idempotent       | Structured findings plus a per-tool summary                                    |
| `lintro_format`     | destructive, not idempotent | Unified diffs, changed files, remaining findings                               |
| `lintro_review`     | read-only, not idempotent   | AI review findings plus run and budget metadata                                |
| `lintro_list_tools` | read-only, idempotent       | Every tool with type, languages, install state                                 |
| `lintro_versions`   | read-only, idempotent       | Installed versus expected tool versions                                        |
| `lintro_doctor`     | read-only, idempotent       | `{health, checks, summary}`; each check `{check, status, detail, remediation}` |

Further toolkits register through the internal `McpToolRegistry` and ship in follow-up
issues.

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

### `lintro_review`

Runs the same AI diff review as `lintro review` and returns its findings as data. The
tool is read-only — nothing is written and nothing is posted — but **not idempotent**:
every call issues provider calls and costs money.

Arguments (all optional):

| Argument       | Type       | Default             | Meaning                                                  |
| -------------- | ---------- | ------------------- | -------------------------------------------------------- |
| `base`         | `string`   | `origin/HEAD`       | Base git ref to diff against                             |
| `uncommitted`  | `boolean`  | `false`             | Review working-tree changes instead of a branch diff     |
| `depth`        | `1..3`     | `review.depth`      | 1 checklist, 2 + generated questions, 3 + adversarial    |
| `strictness`   | `string`   | `review.strictness` | `focused`, `balanced`, or `thorough`                     |
| `with_lint`    | `boolean`  | `false`             | Include a lint digest of the changed files in the prompt |
| `paths`        | `string[]` | the whole diff      | Limit the review to these path prefixes                  |
| `max_cost_usd` | `number`   | `ai.max_cost_usd`   | Spend ceiling for this call; can only lower the config   |

```json
{
  "summary": "One blocking issue.",
  "findings": [
    {
      "file": "app.py",
      "line": 3,
      "severity": "P1",
      "category": "correctness",
      "title": "Unbounded loop",
      "body": "The loop never terminates.\n\nCause: ...\n\nFix: ...",
      "confidence": "high",
      "suggested_code": "i += 1",
      "checklist_ids": [2],
      "source": ""
    }
  ],
  "run": {
    "model": "claude-sonnet-4-5",
    "provider": "anthropic",
    "depth": 1,
    "strictness": "balanced",
    "cost_usd": 0.25,
    "duration_seconds": 12.5,
    "chunks": { "total": 2, "reviewed": 2 },
    "files": { "reviewed": 1, "total": 1 },
    "token_usage": { "prompt": 10, "completion": 5, "total": 15 },
    "partial": false,
    "stopped_reason": ""
  },
  "budget": {
    "requested_usd": null,
    "configured_usd": 1.0,
    "effective_usd": 1.0,
    "clamped": false,
    "exceeded": false
  }
}
```

Cost control:

- `ai.max_cost_usd` in the workspace config is the ceiling. `max_cost_usd` can only
  **lower** it; a larger value is clamped to the configured one and reported as
  `budget.clamped: true`. If the operator sets no ceiling, the argument becomes the
  ceiling — set `ai.max_cost_usd` if agents must never spend more than a fixed amount.
- When the ceiling stops a run **after** some chunks were reviewed, the call succeeds
  with what was found: `run.partial: true`, `run.stopped_reason`,
  `budget.exceeded: true`.
- When it stops the run **before any chunk** was reviewed, no review was produced, and
  the call fails with `budget_exceeded` rather than an empty "clean" result.

Notes and limits:

- `--post` (GitHub commenting) is deliberately not exposed. The calling agent owns
  outward side effects.
- Read-only covers the reviewed tree. The one file a review can create is an AI
  transcript, and only when the operator enabled `ai.transcript_logging` (or
  `LINTRO_AI_TRANSCRIPT=1`); it is written under the gitignored `.lintro-cache/`, and no
  tool argument can turn it on.
- An empty diff is a result, not an error: `findings: []` with zero-valued run metadata.
- Without the `[ai]` extra, without a usable provider, or with `ai.review: false`, the
  tool is still listed and returns `tool_unavailable` with `detail.reason` so an agent
  gets a reason rather than a missing capability.
- A failed review carries the same taxonomy `lintro review --output json` prints, under
  `detail.review_error` (`kind`, `provider`, `status`, `retryable`).
- **Latency**: a depth-3 review runs for minutes. Progress notifications are not sent;
  the call's `timeout_seconds` is 1800s instead of the 300s default, and the review
  holds the server's run lock for its whole duration, so `lintro_check` calls queue
  behind it.

### `lintro_list_tools`

Takes no arguments. Answers "what can lintro do in this workspace, and what is actually
installed" in one call, so an agent does not have to discover a missing binary by
failing a `lintro_check`.

```json
{
  "tools": [
    {
      "name": "ruff",
      "description": "Fast Python linter and formatter replacing multiple tools",
      "types": ["linter", "formatter"],
      "languages": ["python"],
      "installed": true,
      "version": "0.16.0",
      "expected_version": "0.15.9",
      "minimum_version": "0.15.9",
      "status": "ok",
      "can_fix": true,
      "capabilities": ["check", "fix"],
      "execution_class": "deterministic",
      "origin": "builtin",
      "profile_membership": ["complete", "full", "minimal", "python"],
      "install_hint": "uv pip install 'ruff>=0.15.9'"
    }
  ],
  "profiles": [
    {
      "name": "minimal",
      "description": "Core Python linting only (fast CI)",
      "strategy": "explicit",
      "resolution": "static"
    }
  ],
  "summary": { "total": 38, "installed": 36, "missing": 2 }
}
```

- A tool that is not installed is listed with `installed: false`, `version: null`, its
  `status` (`missing`, `outdated`, `incompatible`, `unknown`) and an `install_hint` —
  never omitted. An absent entry would be indistinguishable from a tool lintro does not
  support.
- `types` is a list, not a scalar: `ToolType` is a bitmask and tools such as `ruff` and
  `oxlint` are genuinely both a linter and a formatter.
- `capabilities` is what you may call the tool with: `check`, `fix`, or `review` for
  advisory AI finders, which are unreachable from `lintro_check` by design.
- `profile_membership` lists only the install profiles whose membership is fixed by the
  manifest. Profiles resolved against the languages detected in a tree (`recommended`,
  `ci`) are reported in `profiles` with `resolution: "workspace"` instead, because
  membership in them is a property of the workspace rather than of the tool.

### `lintro_versions`

Takes no arguments. The compatibility view of the same data: what is installed against
what lintro requires.

```json
{
  "tools": [
    {
      "name": "ruff",
      "installed_version": "0.9.0",
      "minimum_version": "0.15.9",
      "recommended_version": "0.15.9",
      "satisfies_minimum": false,
      "below_recommended": false,
      "status": "outdated",
      "error": "Version 0.9.0 is below minimum requirement 0.15.9",
      "install_hint": "uv pip install 'ruff>=0.15.9'"
    }
  ],
  "summary": { "ok": 28, "outdated": 7, "missing": 2, "total": 37 }
}
```

`status` is `ok`, `outdated`, or `missing`. `missing` covers every way a probe fails to
yield a version — absent binary, non-zero exit, unparseable output — with `error`
carrying the reason. A version below the minimum is data, not an error: the call
succeeds.

### `lintro_doctor`

Takes no arguments. The same probes `lintro doctor` renders, as records an agent or a
host UI can act on.

```json
{
  "health": "degraded",
  "checks": [
    {
      "check": "config.load",
      "status": "ok",
      "detail": "Configuration loaded from /path/to/repo/.lintro-config.yaml",
      "remediation": "",
      "category": "config"
    },
    {
      "check": "tools.missing",
      "status": "error",
      "detail": "1 enabled tool(s) not installed: hadolint",
      "remediation": "lintro install hadolint",
      "category": "tools"
    }
  ],
  "summary": { "ok": 4, "warning": 1, "error": 1, "skipped": 1, "total": 7 }
}
```

- `status` is `ok`, `warning`, `error`, or `skipped`. `health` is `healthy` when nothing
  warned or errored, `degraded` otherwise; `skipped` never counts against it.
- Categories: `config` (loading and consistency), `tools` (`tools.missing`,
  `tools.versions`), `ai` (one check per provider/auth probe), `extras` (`extras.mcp` —
  an uninstalled optional extra is a fact, never a failure).
- Per-tool installation detail deliberately lives in `lintro_list_tools`; the doctor
  report answers "is anything wrong, and what fixes it".
- No provider call is made: AI checks are presence checks only, the same set
  `lintro doctor` runs without `--ai-liveness`.
- A malformed config degrades the report (`config.load` with `status: "error"`) instead
  of failing the call.

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

Codes: `workspace_violation`, `tool_unavailable`, `invalid_input`, `execution_error`,
`budget_exceeded`. `detail` is optional and may be `null`. A handler that exceeds its
time budget (`timeout_seconds`, 300s by default) reports `execution_error` with
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
