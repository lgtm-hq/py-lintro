# Watch Mode

`lintro watch` continuously monitors your files and re-runs the relevant tools whenever
something changes, giving you instant feedback while you code instead of switching back
to a terminal to run `lintro check`.

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

Press `Ctrl+C` to stop watching. Watch mode shuts down cleanly and flushes any in-flight
run before exiting.

## How It Works

```console
$ lintro watch src/
👀 Watching for changes in src/...
Press Ctrl+C to stop

[12:34:56] changed: src/foo.py
  ... tool results ...

[12:35:12] changed: src/bar.py
  ... tool results ...
```

### Smart Tool Selection

Only tools relevant to the files that changed are run. The mapping is derived from each
tool's own file patterns, so it stays in sync with the tool registry automatically:

| Changed file | Tools run (examples)      |
| ------------ | ------------------------- |
| `*.py`       | ruff, mypy, bandit, black |
| `*.ts`       | oxlint, tsc               |
| `*.rs`       | clippy, rustfmt           |
| `*.yaml`     | yamllint, prettier        |

Catch-all scanners (`*` patterns such as gitleaks, trufflehog, commitlint, typos) and
advisory review finders are skipped unless you name them in `--tools` or `watch.tools`.
pytest is never selected; use `lintro test`.

Use `--tools` to further narrow the set that runs. `--fix` keeps only tools that can
format.

### Debouncing

Editors and tools (formatters, `git checkout`, bulk saves) often emit many filesystem
events in quick succession. Watch mode waits for a short quiet period (300 ms by
default) before running, so a burst of edits triggers a single run rather than one per
keystroke. Tune it with `--debounce`:

```bash
lintro watch --debounce 500
```

### Ignored Paths

By default, watch mode ignores noisy or irrelevant locations such as `.git/`,
`__pycache__/`, tool caches, `node_modules/`, virtualenvs, and build output.
Configuration `watch.ignore` **extends** those built-ins; it cannot re-enable `.git/` or
`node_modules/` by replacing the list. An empty `ignore` keeps the defaults.
`--include-venv` drops the built-in virtualenv ignores (including `.venv`, `venv`,
`env`, `virtualenv`, and `site-packages`) so those directories can produce events.

## Options

| Flag                     | Description                                                |
| ------------------------ | ---------------------------------------------------------- |
| `--tools`                | Tool allowlist; `all` uses smart selection.                |
| `--fix` / `--no-fix`     | Force fix mode on or off (overrides `watch.auto_fix`).     |
| `--clear` / `--no-clear` | Force screen clear on or off (overrides config).           |
| `--debounce`             | Debounce interval in milliseconds (default `300`).         |
| `--exclude`              | Comma-separated exclude patterns passed to the tools.      |
| `--include-venv`         | Include virtual environment directories.                   |
| `--output-format`        | Output format: `plain`, `grid`, `markdown`, `json`, `csv`. |

## Configuration

Watch defaults can be set under a `watch:` section in `.lintro-config.yaml` (or
`[tool.lintro.watch]` in `pyproject.toml`). Present CLI flags override configuration;
`--no-fix` / `--no-clear` force those booleans off.

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
    - '**/__pycache__/**'
    - '**/.git/**'
    - '**/node_modules/**'
```

| Key            | Type      | Default | Description                                         |
| -------------- | --------- | ------- | --------------------------------------------------- |
| `debounce_ms`  | int       | `300`   | Quiet period before a run (must be `>= 0`).         |
| `auto_fix`     | bool      | `false` | Run tools in fix mode.                              |
| `clear_screen` | bool      | `false` | Clear the terminal between runs.                    |
| `tools`        | list[str] | `[]`    | Allowlist; empty or `all` uses smart selection.     |
| `ignore`       | list[str] | `[]`    | Extra gitignore-style patterns (extends built-ins). |

## Notes

- Watching a single file watches its parent directory recursively so renames and
  re-creates are picked up.
- Watch mode uses the [`watchdog`](https://pypi.org/project/watchdog/) library for
  efficient, cross-platform native filesystem notifications.
