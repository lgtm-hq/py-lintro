# Buf Tool Analysis

## Overview

[buf](https://buf.build/) is a modern toolkit for Protocol Buffers. Lintro integrates
two of its capabilities:

- **`buf lint`** — an extensive catalog of protobuf lint rules covering naming
  conventions, package layout, and RPC/enum best practices.
- **`buf format`** — a deterministic protobuf formatter, used both to detect unformatted
  files and to rewrite them in place.

buf is registered as a combined **linter + formatter** (`can_fix=True`) with priority
`50`, matching `*.proto` files. Native configs: `buf.yaml`, `buf.work.yaml`.

## Installation

```bash
brew install bufbuild/buf/buf
# or
brew install buf
# or download a release binary
#   https://github.com/bufbuild/buf/releases
```

Lintro's `install-tools.sh` installs the pinned binary from GitHub releases
(`buf-$(uname -s)-$(uname -m)`) with SHA-256 checksum verification against the release
`sha256.txt`.

## Default configuration behavior

buf works **with or without** a `buf.yaml`. With no module config present, buf lints
against its `STANDARD` default rule set and uses the current directory as the module
root. Because bare `.proto` files lint cleanly against sensible defaults, lintro runs
buf directly rather than skipping when no `buf.yaml` is found — this mirrors how the
other config-optional tools (ruff, taplo) behave.

Adding a `buf.yaml` lets you select rule categories or opt into extra ones:

| Category    | Purpose                                             |
| ----------- | --------------------------------------------------- |
| `MINIMAL`   | Structural rules (package defined, no import cycle) |
| `BASIC`     | MINIMAL + naming/style basics                       |
| `STANDARD`  | BASIC + directory/version/RPC conventions (default) |
| `COMMENTS`  | Requires documentation comments                     |
| `UNARY_RPC` | Disallows streaming RPCs                            |

## Output format and parser choice

`buf lint --error-format json` emits **newline-delimited JSON** (one object per
violation) on stdout:

```json
{
  "path": "v.proto",
  "start_line": 2,
  "start_column": 1,
  "end_line": 2,
  "end_column": 19,
  "type": "PACKAGE_LOWER_SNAKE_CASE",
  "message": "Package name \"MyPackage\" should be lower_snake.case, such as \"my_package\"."
}
```

Parse/compile errors share the same JSON shape with `type` set to `COMPILE`.

### Native parser vs. shared SARIF ingestion

buf **1.71.0 does not emit SARIF**. Its `--error-format` options are
`text, json, msvs, junit, github-actions, gitlab-code-quality, config-ignore-yaml` — no
SARIF. Applying the fidelity checklist from the SARIF ingestion evaluation (issue #1066
/ PR #1140) is therefore moot: there is no SARIF stream to ingest, so a **native JSON
parser** is the only faithful option. The native parser preserves everything buf reports
— the full start/end position range (`start_line`/`start_column`/
`end_line`/`end_column`), the rule id (`type`), and the message. A hypothetical SARIF
bridge would add nothing here and would drop buf's exact end-position range unless every
field were remapped by hand.

The parser lives in `lintro/parsers/buf/`:

- `parse_buf_output()` — parses the JSONL lint stream; malformed lines are skipped
  rather than aborting the whole report.
- `parse_buf_format_output()` — parses `buf format -d` unified diffs into one `FORMAT`
  issue per unformatted file.

Lint categories are **not** included in buf's per-violation JSON (only the rule id is),
so lintro reports the rule id as the issue `code` and does not fabricate a category. The
category → rule mapping is available separately via `buf config ls-lint-rules`.

## Fix path

buf ships a formatter, so lintro implements the fix path:

- **Check:** `buf format --diff --exit-code` reports a non-zero exit and a unified diff
  when files are not formatted. Each unformatted file becomes a `FORMAT` issue.
- **Fix:** `buf format --write` rewrites files in place. Lint violations are not
  auto-fixable and are reported as remaining issues, preserving the lintro invariant
  `initial = fixed + remaining`.

## Module roots and directory-based rules

lintro invokes buf from the common parent directory of the `.proto` files it selects,
passing that directory as the module input (`.`) and restricting the run to the selected
files via `--path`. buf's directory-based rules — notably `PACKAGE_DIRECTORY_MATCH` —
resolve package paths relative to that module root. When packages are laid out as nested
directories (e.g. `foo/v1/foo.proto` with `package foo.v1`), run lintro from the
repository/module root (or add a `buf.yaml`) so those rules see the expected layout.

## Usage

```bash
# Lint .proto files
lintro check --tools buf

# Format .proto files in place
lintro format --tools buf

# Point buf at an explicit config
lintro check --tools buf --tool-options buf:config=proto/buf.yaml

# Do not follow symlinks
lintro check --tools buf --tool-options buf:disable_symlinks=true
```

## Options

| Option             | Type    | Description                                     |
| ------------------ | ------- | ----------------------------------------------- |
| `config`           | string  | Path to a `buf.yaml` file or inline config data |
| `disable_symlinks` | boolean | Do not follow symlinks when reading sources     |
| `timeout`          | int     | Per-invocation timeout in seconds (default 30)  |

## References

- buf lint rules: <https://buf.build/docs/lint/rules/>
- buf format: <https://buf.build/docs/reference/cli/buf/format>
- buf repository: <https://github.com/bufbuild/buf>
