# SwiftLint Tool Analysis

## Overview

SwiftLint is the de-facto linter for Swift, maintained by Realm. It enforces Swift style
and conventions with 200+ built-in rules spanning code style, potential bugs (lint),
metrics (complexity/length), performance, and idiomatic Swift. It runs on `*.swift`
files and can auto-correct many rules.

## Core Tool Capabilities

- Runs via `swiftlint lint` against files or directories
- Emits structured diagnostics through pluggable reporters, including `--reporter json`
  and `--reporter sarif`
- Auto-corrects many rules via `swiftlint --fix`
- Configurable via a project `.swiftlint.yml` / `.swiftlint.yaml`
- Custom regex rules and rule opt-in/opt-out

## Rule Categories

- **Lint** — potential bugs and correctness issues
- **Style** — formatting and idiomatic conventions (e.g., `identifier_name`,
  `type_name`, `trailing_semicolon`)
- **Metrics** — complexity and length thresholds (e.g., `line_length`)
- **Performance** and **idiomatic** rules

## Parser choice: native JSON (not shared SARIF)

Per the SARIF ingestion evaluation (`docs/design/sarif-ingestion-evaluation.md`, Refs
Issue Issue #1066 / PR #1140), a tool should use the shared SARIF parser only when its SARIF output
is lossless for lintro's model. SwiftLint 0.65.0 emits **both** `--reporter json` and
`--reporter sarif`; both were captured from the same run and compared against the
fidelity checklist:

| Field / concern      | JSON reporter                                                                   | SARIF reporter                                                                                                           |
| -------------------- | ------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| file / line / column | `file`, `line`, `character` — present                                           | `physicalLocation` — present                                                                                             |
| rule id              | `rule_id` — present                                                             | `ruleId` — present                                                                                                       |
| severity             | `severity` (`Error`/`Warning`) — present                                        | `level` (`error`/`warning`) — present                                                                                    |
| **rule category**    | `type` (e.g., `Identifier Name`) — **present per result**                       | **absent** from `results[]`; only a `shortDescription` in the run-level `rules[]` catalog                                |
| fix metadata         | absent (neither reporter encodes autocorrectability)                            | absent (`fixes[]` not emitted)                                                                                           |
| doc URL              | not inline (lintro synthesizes `https://realm.github.io/SwiftLint/<rule>.html`) | `helpUri` in the `rules[]` catalog                                                                                       |
| payload size         | compact (one array of findings)                                                 | embeds the **full 255-rule catalog** on every run regardless of finding count (~20 KB+ even for a handful of violations) |

**Decision: native JSON parser.** SARIF is lossy here relative to JSON — it drops the
per-result `type` category that lintro surfaces, and it bloats output by embedding the
entire rule catalog on every invocation. What SARIF adds (`helpUri`) lintro already
reconstructs deterministically from the rule id via `DocUrlTemplate.SWIFTLINT`. The
shared SARIF parser also remains an unwired proof-of-concept (no tool consumes it), so
adopting it here would be premature. The JSON reporter is richer per finding, more
compact, and matches the reference plugin pattern (clippy/shellcheck).

## Lintro Implementation Analysis

### ✅ Preserved Features

- Executes `swiftlint lint --reporter json --quiet <file>` for checks
- Auto-corrects via `swiftlint --fix --quiet <file>`, then re-checks to report the
  issues that remain (many rules such as `identifier_name`/`type_name` are not
  auto-correctable)
- Parses the JSON array into structured issues (`file`, `line`, `column`, `rule_id`,
  `severity`, `type`, `reason`)
- Honors a project `.swiftlint.yml` / `.swiftlint.yaml` discovered from the working
  directory (no config is forced by lintro)

### ⚠️ Defaults and Notes

- Runs with SwiftLint's defaults; no rule set is imposed by lintro
- SwiftLint exits non-zero whenever violations are present; the plugin still parses the
  emitted JSON. A non-zero exit with **no** parsed issues is treated as a genuine
  failure and the raw output is surfaced
- Default timeout is 60s (configurable via the `timeout` option)

### 🚀 Enhancements

- Normalized `ToolResult` with issue counts and fix metrics
  (`initial == fixed + remaining`)
- Deterministic per-rule documentation URLs via `DocUrlTemplate.SWIFTLINT`
- Severity normalized through lintro's shared `SeverityLevel` alias table

## Installation

- **macOS:** `brew install swiftlint` (the Swift runtime is already present)
- **Linux:** download the fully **static** binary from the release archive —
  `swiftlint_linux_<amd64|arm64>.zip` ships a `swiftlint-static` executable that
  requires **no** Swift runtime. `scripts/utils/install-tools.sh` fetches this static
  binary, and the Docker image installs and verifies it the same way

### Linux / Docker story

Historically SwiftLint's Linux binaries linked dynamically against the Swift runtime,
which made slim-image installs impractical. Recent releases (including 0.65.0)
additionally ship a statically linked `swiftlint-static` binary for both `amd64` and
`arm64`. Because it is self-contained, lintro installs it in the Debian-slim Docker
image (`install-tools.sh` extracts `swiftlint-static` and renames it to `swiftlint`) and
verifies it in the image build — no Swift toolchain is added and the image build is not
broken.

## Configuration Strategy

- Minimum version from `manifest.json` / `_tool_versions.py` (`SWIFTLINT`)
- File patterns: `*.swift`
- Native configs: `.swiftlint.yml`, `.swiftlint.yaml`
- Timeout configurable via tool options

## Usage Comparison

### Core SwiftLint

```bash
swiftlint lint --reporter json --quiet Sources/
swiftlint --fix Sources/
```

### Lintro Wrapper

```bash
uv run lintro check --tools swiftlint Sources/
uv run lintro format --tools swiftlint Sources/
```

## ⚠️ Limited/Missing Features

- No pass-through for arbitrary SwiftLint flags (e.g., `--strict`) yet
- Auto-correct safety metadata is not modeled (SwiftLint does not expose it in either
  the JSON or SARIF reporter)

## Recommendations

- Consider a `strict` option mapping to `--strict` if consumers want warnings to fail
  the run
- Revisit shared-SARIF ingestion only if SwiftLint later emits per-result categories and
  fix metadata in its SARIF output
