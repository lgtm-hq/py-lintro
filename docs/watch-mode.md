# Watch Mode

`lintro watch` continuously monitors your files and re-runs the relevant
tools whenever something changes, giving you instant feedback while you code
instead of switching back to a terminal to run `lintro check`.

## Quick Start

```bash
# Watch the current directory
lintro watch

# Watch specific paths
lintro watch src/ tests/

# Watch and limit to specific tools
lintro watch --tools ruff,mypy

# Automatically fix issues on save
lintro watch --fix
```

Press `Ctrl+C` to stop watching. Watch mode shuts down cleanly and flushes any
in-flight run before exiting.

## How It Works

```
$ lintro watch src/
👀 Watching for changes in src/...
Press Ctrl+C to stop

[12:34:56] changed: src/foo.py
  ... tool results ...

[12:35:12] changed: src/bar.py
  ... tool results ...
```

### Smart Tool Selection

Only tools relevant to the files that changed are run. The mapping is derived
from each tool's own file patterns, so it stays in sync with the tool registry
automatically:

| Changed file | Tools run (examples)       |
| ------------ | -------------------------- |
| `*.py`       | ruff, mypy, bandit, black  |
| `*.ts`       | oxlint, tsc                |
| `*.rs`       | clippy, rustfmt            |
| `*.yaml`     | yamllint, prettier         |

Use `--tools` to further narrow the set that runs.

### Debouncing

Editors and tools (formatters, `git checkout`, bulk saves) often emit many
filesystem events in quick succession. Watch mode waits for a short quiet
period (300 ms by default) before running, so a burst of edits triggers a
single run rather than one per keystroke. Tune it with `--debounce`:

```bash
lintro watch --debounce 500
```

### Ignored Paths

By default, watch mode ignores noisy or irrelevant locations such as
`.git/`, `__pycache__/`, tool caches, `node_modules/`, virtualenvs, and build
output. Override the list via configuration (see below).

## Options

| Flag              | Description                                             |
| ----------------- | ------------------------------------------------------- |
| `--tools`         | Comma-separated allowlist of tools to run.              |
| `--fix`           | Run tools in fix mode instead of check-only.            |
| `--clear`         | Clear the screen between runs for cleaner output.       |
| `--debounce`      | Debounce interval in milliseconds (default `300`).      |
| `--exclude`       | Comma-separated exclude patterns passed to the tools.   |
| `--include-venv`  | Include virtual environment directories.                |
| `--output-format` | Output format: `plain`, `grid`, `markdown`, `json`, `csv`. |

## Configuration

Watch defaults can be set under a `watch:` section in
`.lintro-config.yaml` (or `[tool.lintro.watch]` in `pyproject.toml`). CLI flags
always override configuration.

```yaml
# .lintro-config.yaml
watch:
  debounce_ms: 300
  auto_fix: false
  clear_screen: false
  tools:
    - ruff
    - mypy
  ignore:
    - "**/__pycache__/**"
    - "**/.git/**"
    - "**/node_modules/**"
```

| Key           | Type         | Default | Description                                    |
| ------------- | ------------ | ------- | ---------------------------------------------- |
| `debounce_ms` | int          | `300`   | Quiet period before a run (must be `>= 0`).    |
| `auto_fix`    | bool         | `false` | Run tools in fix mode.                         |
| `clear_screen`| bool         | `false` | Clear the terminal between runs.               |
| `tools`       | list[str]    | `[]`    | Allowlist of tools (empty = smart selection).  |
| `ignore`      | list[str]    | `[]`    | Gitignore-style ignore patterns (empty = built-in defaults). |

## Notes

- Watching a single file watches its parent directory recursively so renames
  and re-creates are picked up.
- Watch mode uses the [`watchdog`](https://pypi.org/project/watchdog/) library
  for efficient, cross-platform native filesystem notifications.
