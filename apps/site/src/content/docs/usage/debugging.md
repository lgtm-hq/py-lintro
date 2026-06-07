---
title: 'debugging'
description:
  "This guide explains how to use Lintro's logging system to diagnose issues."
category: usage
order: 70
navGroup: extend
---

# Debugging Lintro

This guide explains how to use Lintro's logging system to diagnose issues.

## Quick Start

```bash
# Show DEBUG logs on console
lintro check --debug .

# Run with debug logging for a specific tool
lintro check --debug --tools ruff .
```

## Log Locations

### Console Output

| Mode      | Log Level | What You See                                   |
| --------- | --------- | ---------------------------------------------- |
| Default   | WARNING+  | Tool failures, parse errors, permission issues |
| `--debug` | DEBUG+    | Full execution trace, commands, file counts    |

### File Logs

Debug logs are always written to file, regardless of console verbosity:

```text
~/.lintro/run-<timestamp>/debug.log
```

Each run creates a new timestamped directory containing:

- `debug.log` - Full DEBUG+ logs with timestamps
- Output files (JSON, CSV, etc.) if requested

### Log Rotation

- **Max files**: 5 rotated log files
- **Max size**: 100MB per file
- **Total**: Up to 500MB of logs retained

## Log Levels

| Level   | When Used          | Example                                         |
| ------- | ------------------ | ----------------------------------------------- |
| DEBUG   | Execution details  | `[ruff] Ready: 45 files, timeout=30s`           |
| WARNING | Recoverable issues | `Failed to parse .prettierrc: Unexpected token` |
| ERROR   | Critical failures  | `Command not found: ruff`                       |

## What Gets Logged

### Tool Execution (DEBUG)

```text
[ruff] Preparing execution for 3 input paths
File discovery complete: 45 files matching ['*.py']
[ruff] Ready: 45 files, timeout=30s
Running subprocess: ruff check --output-format json... (timeout=30s, cwd=/project)
```

### Subprocess Failures (DEBUG + WARNING)

```text
# DEBUG - stderr preview on non-zero exit
Subprocess ruff exited with code 1, stderr: error: invalid config...

# WARNING - timeouts and missing commands
Subprocess ruff timed out after 30s
Command not found: ruff. Ensure it is installed and in PATH.
```

### Config Parse Errors (WARNING)

```text
Failed to parse JSON config .prettierrc: Expecting property name (line 5, col 3)
Failed to parse pyproject.toml at /project/pyproject.toml: Invalid value
```

### Permission Issues (WARNING)

```text
Cannot write to /project/.lintro (permission denied), using fallback: /tmp/lintro/run-...
```

## Common Debug Scenarios

### Tool Not Running

**Symptom**: Tool shows "skipped" or doesn't appear in output.

**Debug**:

```bash
lintro check --debug --tools <tool_name> .
```

**Look for**:

- `Skipping <tool>: version check failed` - Tool not installed or wrong version
- `[<tool>] Early exit: No files to check` - No matching files found
- `Command not found: <tool>` - Tool binary not in PATH

### Silent Failures

**Symptom**: Tool reports ERROR status but no issues shown.

**Debug**:

```bash
lintro check --debug .
# Then check the debug log
cat ~/.lintro/run-*/debug.log | grep -A5 "failed\|error\|stderr"
```

**Look for**:

- `Subprocess <tool> exited with code N, stderr: ...` - Tool execution error
- `Failed to parse <config>` - Config file is malformed

### Slow Execution

**Symptom**: Lintro takes a long time or times out.

**Debug**:

```bash
lintro check --debug .
```

**Look for**:

- `File discovery complete: N files` - Too many files being scanned?
- `Subprocess <tool> timed out after Ns` - Increase timeout with
  `--tool-options tool:timeout=N`

### Config Not Applied

**Symptom**: Tool doesn't respect your config file.

**Debug**:

```bash
lintro config --verbose
lintro check --debug .
```

**Look for**:

- `Discovered <tool> config at ...` - Config file found
- `Failed to parse <config>` - Config file has syntax errors

## Environment Variables

| Variable       | Purpose                                                       |
| -------------- | ------------------------------------------------------------- |
| `LINTRO_DEBUG` | Set to `1` to enable debug logging (alternative to `--debug`) |
| `NO_COLOR`     | Set to `1` to disable colored output                          |

## Getting Help

If debug logs don't help diagnose the issue:

1. Run with `--debug` and save the output
2. Check `~/.lintro/run-*/debug.log` for the full trace
3. [Open an issue][issues] with:
   - Lintro version (`lintro --version`)
   - Tool versions (`ruff --version`, etc.)
   - Relevant debug log excerpts
   - Steps to reproduce

[issues]: https://github.com/lgtm-hq/py-lintro/issues
