# dotenv-linter Tool Analysis

## Overview

[dotenv-linter](https://dotenv-linter.github.io/) is a fast, Rust-based linter and fixer
for `.env` files. It detects common mistakes such as duplicate keys, lowercase keys,
incorrect delimiters, unordered keys, and stray whitespace, and can auto-fix most of
them in place. This analysis documents Lintro's wrapper implementation and how it maps
onto the core tool.

## Core Tool Capabilities

dotenv-linter (v4.0.0) is invoked through subcommands:

- `dotenv-linter check <files>` — report issues (exit code `1` when problems are found).
- `dotenv-linter fix <files>` — auto-fix issues in place (creates `.env.bak` backups
  unless `--no-backup` is passed; `--dry-run` prints the fixed content instead).
- `dotenv-linter diff <files>` — compare key sets across files.

Checks include: `DuplicatedKey`, `EndingBlankLine`, `ExtraBlankLine`,
`IncorrectDelimiter`, `KeyWithoutValue`, `LeadingCharacter`, `LowercaseKey`,
`QuoteCharacter`, `SchemaViolation`, `SpaceCharacter`, `SubstitutionKey`,
`TrailingWhitespace`, `UnorderedKey`, and `ValueWithoutQuotes`.

### Output format

`check --plain` emits one diagnostic per line plus a header and summary:

```text
Checking .env
.env:2 LowercaseKey: The foo key should be in uppercase
.env:3 KeyWithoutValue: The BAR key should be with a value or have an equal sign
.env:4 LeadingCharacter: Invalid leading character detected

Found 3 problems
```

Diagnostic lines follow the shape `filename:line CheckName: message`. There is **no
column** component, and (as of v4.0.0) **no SARIF or JSON output** in any released
version.

## Parser Choice: Native Parser

Following the SARIF ingestion evaluation guidance (issue #1066 / PR #1140): the shared
SARIF parser is only used when a tool emits SARIF losslessly. dotenv-linter emits **no
SARIF or structured output at all** — only plain-text (`--plain`) or ANSI-colored
diagnostics. A native line parser is therefore required.

`lintro/parsers/dotenv_linter/dotenv_linter_parser.py` parses the plain-text lines with
a single regex, strips ANSI codes defensively, and ignores the `Checking …` header and
`Found N problems` / `No problems found` summary lines. It captures the check name
(`code`), line number, and message with full fidelity. Because there is no upstream
SARIF, nothing is "dropped" relative to a SARIF path — the native parser is strictly
more faithful than any SARIF ingestion would be.

## Lintro Implementation Analysis

### Preserved Features

- **Check mode**: runs `dotenv-linter check --plain` per file and parses all findings.
- **Fix mode**: runs `dotenv-linter fix --no-backup --plain` per file, bracketed by a
  pre-fix check and a post-fix re-check so the `initial == fixed + remaining` ToolResult
  invariant holds even for checks that cannot be auto-fixed.
- **Check name as code**: each issue carries the dotenv-linter check name, surfaced in
  the unified table's `code` column.
- **Documentation links**: `doc_url()` converts a CamelCase check name to the snake_case
  anchor used by the docs site, e.g. `LowercaseKey` →
  `https://dotenv-linter.github.io/#/checks/lowercase_key`.
- **Options**: `recursive`, `exclude`, `skip_checks` (maps to `--ignore-checks`), and
  `schema` (maps to `--schema`).

### Notable Behaviors

- **No column data**: dotenv-linter does not report columns, so the `column` field is
  always `0` (rendered as `-`).
- **Backups suppressed**: fix always passes `--no-backup` so Lintro does not leave
  `.env.bak` artifacts in the working tree.
- **Severity**: dotenv-linter treats all findings uniformly; Lintro maps them to the
  `warning` severity level.

## Installation

- Homebrew: `brew install dotenv-linter`
- Cargo: `cargo install dotenv-linter`
- Binary release: <https://github.com/dotenv-linter/dotenv-linter/releases> (v4.0.0+)

## File Targeting

Lintro discovers `.env`, `.env.*`, and `*.env` files. Note that repositories commonly
`.gitignore` real `.env` files; Lintro's own `.lintro-ignore` also excludes
`test_samples/`, so the bundled sample fixtures are only exercised via the binary-gated
integration tests.
